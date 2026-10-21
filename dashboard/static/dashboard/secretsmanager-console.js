/* global ServiceConsole */

const SecretsManagerConsole = (() => {
  const consoleUi = window.ServiceConsole;
  const root = document.getElementById('secretsmanager-console-root');
  const breadcrumbsEl = document.getElementById('secretsmanager-breadcrumbs');
  const summaryEl = document.getElementById('secretsmanager-summary');
  const loadedAtEl = document.getElementById('secretsmanager-loaded-at');

  const state = {
    inventory: null,
    selectedSecretName: '',
    activeView: 'overview',
    resourceQuery: '',
    selectedVersionId: '',
    lastResult: null,
    revealed: null,
  };

  const el = consoleUi.el;
  const apiJson = consoleUi.apiJson;
  const btn = consoleUi.button;
  const toast = (message, isError = false) => consoleUi.toast(message, {
    classPrefix: 'secretsmanager',
    type: isError ? 'error' : 'success',
  });
  const toolbar = (leftItems, rightItems) => consoleUi.toolbar(leftItems, rightItems, 'secretsmanager');
  const openModal = (title, bodyNode, confirmLabel, onConfirm) =>
    consoleUi.openModal(title, bodyNode, confirmLabel, onConfirm, {
      classPrefix: 'secretsmanager',
      toast,
    });

  function secrets() {
    return state.inventory?.secrets || [];
  }

  function secretName(secret) {
    return secret?.name || secret?.arn || '';
  }

  function selectedSecret() {
    return secrets().find((secret) => secretName(secret) === state.selectedSecretName) || secrets()[0] || null;
  }

  function secretPath(secret) {
    return encodeURIComponent(secretName(secret));
  }

  function parseSecretValue(value) {
    const trimmed = String(value || '').trim();
    if (!trimmed) {
      throw new Error('Secret value is required');
    }
    if (!['{', '['].includes(trimmed[0])) {
      return trimmed;
    }
    return JSON.parse(trimmed);
  }

  function parseJson(value, fallback, label) {
    const text = String(value || '').trim();
    if (!text) return fallback;
    try { return JSON.parse(text); } catch (error) { throw new Error(`${label} must be valid JSON`); }
  }

  function parseList(value) {
    return String(value || '').split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean);
  }

  function matchesQuery(...values) {
    const query = state.resourceQuery.trim().toLowerCase();
    return !query || values.some((value) => JSON.stringify(value ?? '').toLowerCase().includes(query));
  }

  function renderBreadcrumbs() {
    if (!breadcrumbsEl) {
      return;
    }
    breadcrumbsEl.textContent = '';
    const home = el('button', null, 'AWS Secrets Manager');
    home.addEventListener('click', () => {
      state.selectedSecretName = secrets()[0] ? secretName(secrets()[0]) : '';
      state.revealed = null;
      render();
    });
    breadcrumbsEl.append(home);
    const secret = selectedSecret();
    if (secret) {
      breadcrumbsEl.append(el('span', null, '/'), el('span', null, secretName(secret)));
    }
  }

  function renderSummary(summary) {
    consoleUi.renderSummary(summary, summaryEl, {
      serviceKey: 'secretsmanager',
      targets: {
        secrets: 'Secrets',
        scheduled_for_deletion: 'Secrets',
        rotation_enabled: 'Secrets',
        versions: 'Secrets',
      },
    });
  }

  function showCreateSecretModal() {
    const form = el('div');
    const nameInput = document.createElement('input');
    nameInput.required = true;
    nameInput.placeholder = '/local/app/database';
    const descriptionInput = document.createElement('input');
    descriptionInput.placeholder = 'Local app database credentials';
    const kmsInput = document.createElement('input');
    kmsInput.placeholder = 'Optional KMS key ID';
    const valueInput = document.createElement('textarea');
    valueInput.className = 'secretsmanager-value-input';
    valueInput.value = JSON.stringify({ username: 'local', password: 'secret' }, null, 2);

    form.append(
      el('label', null, 'Secret name'),
      nameInput,
      el('label', null, 'Description'),
      descriptionInput,
      el('label', null, 'KMS key ID'),
      kmsInput,
      el('label', null, 'Secret value'),
      valueInput,
    );

    openModal('Create secret', form, 'Create secret', async (close) => {
      await apiJson('/api/secretsmanager/secrets/', {
        method: 'POST',
        body: JSON.stringify({
          name: nameInput.value.trim(),
          description: descriptionInput.value.trim(),
          kms_key_id: kmsInput.value.trim(),
          value: parseSecretValue(valueInput.value),
        }),
      });
      close();
      toast('Secret created');
      state.selectedSecretName = nameInput.value.trim();
      state.revealed = null;
      await refresh();
    });
  }

  function showUpdateValueModal(secret) {
    const form = el('div');
    const valueInput = document.createElement('textarea');
    valueInput.className = 'secretsmanager-value-input';
    valueInput.value = state.revealed?.name === secretName(secret)
      ? (typeof state.revealed.json === 'object' && state.revealed.json !== null
        ? JSON.stringify(state.revealed.json, null, 2)
        : state.revealed.value || '')
      : '';
    valueInput.placeholder = '{"username":"local","password":"new-secret"}';
    form.append(
      el('label', null, 'Secret'),
      el('pre', 'secretsmanager-secret-preview', secretName(secret)),
      el('label', null, 'New value'),
      valueInput,
    );

    openModal('Update secret value', form, 'Update value', async (close) => {
      await apiJson(`/api/secretsmanager/secrets/${secretPath(secret)}/value/`, {
        method: 'PUT',
        body: JSON.stringify({ value: parseSecretValue(valueInput.value) }),
      });
      close();
      toast('Secret value updated');
      state.revealed = null;
      await refresh();
    });
  }

  function showDeleteSecretModal(secret) {
    const form = el('div');
    const recoveryInput = document.createElement('input');
    recoveryInput.type = 'number';
    recoveryInput.min = '7';
    recoveryInput.max = '30';
    recoveryInput.value = '7';
    const forceInput = document.createElement('input');
    forceInput.type = 'checkbox';
    const forceLabel = el('label', 'secretsmanager-checkbox');
    forceLabel.append(forceInput, el('span', null, 'Force delete without recovery'));
    form.append(
      el('p', 'secretsmanager-warning', `Schedule ${secretName(secret)} for deletion?`),
      el('label', null, 'Recovery window days'),
      recoveryInput,
      forceLabel,
    );

    openModal('Delete secret', form, 'Delete secret', async (close) => {
      await apiJson(`/api/secretsmanager/secrets/${secretPath(secret)}/value/`, {
        method: 'DELETE',
        body: JSON.stringify({
          recovery_window_days: Number(recoveryInput.value || 7),
          force_delete_without_recovery: forceInput.checked,
        }),
      });
      close();
      toast('Secret deletion scheduled');
      state.revealed = null;
      await refresh();
    });
  }

  function showMetadataModal(secret) {
    const form = el('div'); const description = document.createElement('textarea'); description.value = secret.description || '';
    const kms = document.createElement('input'); kms.value = secret.kms_key_id || '';
    form.append(el('label', null, 'Description'), description, el('label', null, 'KMS key ID'), kms);
    openModal('Update secret metadata', form, 'Save', async (close) => {
      state.lastResult = await apiJson(`/api/secretsmanager/secrets/${secretPath(secret)}/metadata/`, { method: 'PATCH', body: JSON.stringify({ description: description.value, kms_key_id: kms.value }) });
      close(); toast('Secret metadata updated'); await refresh();
    });
  }

  function showTagsModal(secret) {
    const form = el('div'); const tags = document.createElement('textarea'); tags.placeholder = '[{"Key":"env","Value":"local"}]';
    const keys = document.createElement('input'); keys.placeholder = 'env,owner';
    form.append(el('label', null, 'Add tags JSON'), tags, btn('Add tags', null, async () => { await apiJson(`/api/secretsmanager/secrets/${secretPath(secret)}/tags/`, { method: 'POST', body: JSON.stringify({ tags: parseJson(tags.value, [], 'Tags') }) }); toast('Tags added'); await refresh(); }), el('label', null, 'Remove tag keys'), keys, btn('Remove tags', 'secretsmanager-btn-secondary', async () => { await apiJson(`/api/secretsmanager/secrets/${secretPath(secret)}/tags/`, { method: 'DELETE', body: JSON.stringify({ tag_keys: parseList(keys.value) }) }); toast('Tags removed'); await refresh(); }));
    openModal('Secret tags', form, 'Done', (close) => close());
  }

  function showRotationModal(secret) {
    const form = el('div'); const lambdaArn = document.createElement('input'); lambdaArn.value = secret.rotation_lambda_arn || ''; lambdaArn.placeholder = 'Lambda ARN';
    const rules = document.createElement('textarea'); rules.value = JSON.stringify(secret.rotation_rules || { AutomaticallyAfterDays: 30 }, null, 2);
    const immediate = document.createElement('input'); immediate.type = 'checkbox'; immediate.checked = true;
    const immediateLabel = el('label', 'secretsmanager-checkbox'); immediateLabel.append(immediate, el('span', null, 'Rotate immediately'));
    form.append(el('label', null, 'Rotation Lambda ARN'), lambdaArn, el('label', null, 'Rotation rules JSON'), rules, immediateLabel);
    openModal('Configure rotation', form, 'Rotate', async (close) => {
      state.lastResult = await apiJson(`/api/secretsmanager/secrets/${secretPath(secret)}/rotate/`, { method: 'POST', body: JSON.stringify({ rotation_lambda_arn: lambdaArn.value, rotation_rules: parseJson(rules.value, {}, 'Rotation rules'), rotate_immediately: immediate.checked }) });
      close(); toast(immediate.checked ? 'Rotation started' : 'Rotation configured'); await refresh();
    });
  }

  function showRandomPasswordModal() {
    const form = el('div'); const length = document.createElement('input'); length.type = 'number'; length.value = '32'; length.min = '1'; length.max = '4096';
    const punctuation = document.createElement('input'); punctuation.type = 'checkbox';
    const punctuationLabel = el('label', 'secretsmanager-checkbox'); punctuationLabel.append(punctuation, el('span', null, 'Exclude punctuation'));
    const result = el('pre', 'secretsmanager-secret-preview'); result.hidden = true;
    form.append(el('label', null, 'Password length'), length, punctuationLabel, result);
    openModal('Generate random password', form, 'Generate', async () => {
      state.lastResult = await apiJson('/api/secretsmanager/random-password/', { method: 'POST', body: JSON.stringify({ PasswordLength: Number(length.value), ExcludePunctuation: punctuation.checked }) });
      result.hidden = false; result.textContent = state.lastResult.random_password || ''; toast('Password generated');
    });
  }

  async function revealSecret(secret, versionId = '') {
    const query = versionId ? `?version_id=${encodeURIComponent(versionId)}` : '';
    const data = await apiJson(`/api/secretsmanager/secrets/${secretPath(secret)}/value/${query}`);
    state.revealed = data;
    toast('Secret value loaded');
    render();
  }

  function renderSecretRow(secret) {
    const name = secretName(secret);
    const active = name === secretName(selectedSecret());
    const row = el('button', `secretsmanager-secret-row${active ? ' secretsmanager-secret-row-active' : ''}`);
    const flags = [
      secret.deleted ? 'scheduled deletion' : 'active',
      secret.rotation_enabled ? 'rotation on' : 'rotation off',
    ];
    row.append(
      el('span', 'secretsmanager-secret-name', name || 'Unnamed secret'),
      el('span', 'secretsmanager-secret-meta', flags.join(' / ')),
    );
    row.addEventListener('click', () => {
      state.selectedSecretName = name;
      state.revealed = null;
      render();
    });
    return row;
  }

  function renderSecretList() {
    const panel = el('section', 'secretsmanager-panel');
    panel.append(el('div', 'secretsmanager-panel-heading', 'Secrets'));
    const list = el('div', 'secretsmanager-secret-list');
    const visibleSecrets = secrets().filter((secret) => matchesQuery(secretName(secret), secret.description, secret.tags, secret.kms_key_id));
    if (!visibleSecrets.length) {
      list.append(el('div', 'secretsmanager-empty', 'No secrets found.'));
    } else {
      visibleSecrets.forEach((secret) => list.append(renderSecretRow(secret)));
    }
    panel.append(list);
    return panel;
  }

  function renderRevealedValue(secret) {
    if (!state.revealed || state.revealed.name !== secretName(secret)) {
      return el('div', 'secretsmanager-empty secretsmanager-empty-compact', 'Reveal the selected local secret value when you need to debug an app read.');
    }
    const card = el('article', 'secretsmanager-value-card');
    const heading = el('div', 'secretsmanager-value-heading');
    heading.append(
      el('h4', null, 'Revealed value'),
      el('span', 'secretsmanager-secret-meta', `${state.revealed.type} / ${consoleUi.formatBytes(state.revealed.size_bytes)}`),
    );
    const details = document.createElement('dl');
    consoleUi.addField(details, 'Version ID', state.revealed.version_id);
    consoleUi.addField(details, 'Stages', state.revealed.version_stages);
    consoleUi.addField(details, 'JSON', state.revealed.json);
    consoleUi.addField(details, 'Value', state.revealed.value);
    card.append(heading, details);
    return card;
  }

  function renderSecretDetail(secret) {
    const panel = el('section', 'secretsmanager-panel');
    const heading = el('div', 'secretsmanager-panel-heading');
    heading.append(
      el('span', null, secret ? secretName(secret) : 'Secret workbench'),
      el('span', 'secretsmanager-secret-meta', secret ? (secret.deleted ? 'Scheduled for deletion' : 'Active') : 'No secret selected'),
    );
    panel.append(heading);

    const content = el('div', 'secretsmanager-detail');
    if (!secret) {
      content.append(el('div', 'secretsmanager-empty', 'Create a secret or refresh after your app creates one.'));
      panel.append(content);
      return panel;
    }

    const details = document.createElement('dl');
    consoleUi.addField(details, 'ARN', secret.arn);
    consoleUi.addField(details, 'Description', secret.description);
    consoleUi.addField(details, 'KMS key ID', secret.kms_key_id);
    consoleUi.addField(details, 'Created', consoleUi.formatDate(secret.created));
    consoleUi.addField(details, 'Last changed', consoleUi.formatDate(secret.last_changed));
    consoleUi.addField(details, 'Last accessed', consoleUi.formatDate(secret.last_accessed));
    consoleUi.addField(details, 'Last rotated', consoleUi.formatDate(secret.last_rotated));
    consoleUi.addField(details, 'Next rotation', consoleUi.formatDate(secret.next_rotation));
    consoleUi.addField(details, 'Rotation Lambda', secret.rotation_lambda_arn);
    consoleUi.addField(details, 'Rotation rules', secret.rotation_rules);
    consoleUi.addField(details, 'Tags', secret.tags);
    consoleUi.addField(details, 'Secret value', 'Hidden. Select Reveal value to retrieve it.');
    content.append(details);

    const actions = el('div', 'secretsmanager-actions');
    actions.append(
      btn('Reveal value', null, () => revealSecret(secret).catch((error) => toast(error.message, true))),
      btn('Update value', 'secretsmanager-btn-secondary', () => showUpdateValueModal(secret)),
      btn('Edit metadata', 'secretsmanager-btn-secondary', () => showMetadataModal(secret)),
      btn('Tags', 'secretsmanager-btn-secondary', () => showTagsModal(secret)),
    );
    if (secret.deleted) actions.append(btn('Restore secret', null, async () => { await apiJson(`/api/secretsmanager/secrets/${secretPath(secret)}/restore/`, { method: 'POST' }); toast('Secret restored'); await refresh(); }));
    else actions.append(btn('Delete secret', 'secretsmanager-btn-danger', () => showDeleteSecretModal(secret)));
    content.append(actions, renderRevealedValue(secret));
    panel.append(content);
    return panel;
  }

  function renderVersionsPanel(secret) {
    const panel = el('section', 'secretsmanager-panel'); panel.append(el('div', 'secretsmanager-panel-heading', `Versions (${secret.versions?.length || 0})`));
    const body = el('div', 'secretsmanager-card-list');
    (secret.versions || []).forEach((version) => {
      const card = el('article', 'secretsmanager-value-card'); const heading = el('div', 'secretsmanager-value-heading'); heading.append(el('h4', null, version.VersionId), el('span', 'secretsmanager-secret-meta', (version.VersionStages || []).join(', ') || 'No stage'));
      const actions = el('div', 'secretsmanager-actions'); actions.append(btn('Reveal version', 'secretsmanager-btn-secondary', () => revealSecret(secret, version.VersionId).catch((error) => toast(error.message, true))), btn('Make AWSCURRENT', null, async () => { const current = (secret.versions || []).find((item) => (item.VersionStages || []).includes('AWSCURRENT')); await apiJson(`/api/secretsmanager/secrets/${secretPath(secret)}/version-stage/`, { method: 'POST', body: JSON.stringify({ version_stage: 'AWSCURRENT', move_to_version_id: version.VersionId, remove_from_version_id: current?.VersionId || '' }) }); toast('AWSCURRENT moved'); await refresh(); }));
      card.append(heading, actions); body.append(card);
    });
    if (!secret.versions?.length) body.append(el('p', 'secretsmanager-empty', 'No versions found.'));
    body.append(renderRevealedValue(secret)); panel.append(body); return panel;
  }

  function renderRotationPanel(secret) {
    const panel = el('section', 'secretsmanager-panel'); panel.append(el('div', 'secretsmanager-panel-heading', 'Rotation'));
    const body = el('div', 'secretsmanager-detail'); const facts = document.createElement('dl');
    consoleUi.addField(facts, 'Enabled', secret.rotation_enabled); consoleUi.addField(facts, 'Lambda ARN', secret.rotation_lambda_arn); consoleUi.addField(facts, 'Rules', secret.rotation_rules); consoleUi.addField(facts, 'Last rotated', consoleUi.formatDate(secret.last_rotated)); consoleUi.addField(facts, 'Next rotation', consoleUi.formatDate(secret.next_rotation));
    body.append(facts, btn('Configure / rotate', null, () => showRotationModal(secret)), el('p', 'secretsmanager-empty-compact', 'Floci invokes the configured Lambda rotation lifecycle and manages AWSPENDING/AWSCURRENT staging labels.'));
    panel.append(body); return panel;
  }

  function renderValuePanel(secret) {
    const panel = el('section', 'secretsmanager-panel'); panel.append(el('div', 'secretsmanager-panel-heading', 'Secret value'));
    const body = el('div', 'secretsmanager-detail'); body.append(el('p', 'secretsmanager-warning', 'Secret values are revealed only on demand and remain in this browser view until another secret is selected or refreshed.'));
    const actions = el('div', 'secretsmanager-actions'); actions.append(btn('Reveal current value', null, () => revealSecret(secret).catch((error) => toast(error.message, true))), btn('Create new version', 'secretsmanager-btn-secondary', () => showUpdateValueModal(secret)));
    body.append(actions, renderRevealedValue(secret)); panel.append(body); return panel;
  }

  function renderResourceTabs() {
    const tabs = el('div', 'secretsmanager-resource-tabs'); const nav = el('div', 'secretsmanager-resource-tab-buttons');
    [['overview', 'Overview'], ['value', 'Value'], ['versions', 'Versions'], ['rotation', 'Rotation']].forEach(([key, label]) => nav.append(btn(label, state.activeView === key ? 'secretsmanager-btn-active' : 'secretsmanager-btn-secondary', () => { state.activeView = key; render(); })));
    const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Filter secrets'; search.value = state.resourceQuery; search.addEventListener('input', () => { state.resourceQuery = search.value; }); search.addEventListener('change', render); search.addEventListener('keydown', (event) => { if (event.key === 'Enter') render(); });
    tabs.append(nav, search); return tabs;
  }

  function renderWorkbench() {
    const secret = selectedSecret();
    const container = el('div');
    container.append(toolbar(
      [
        btn('Create secret', null, showCreateSecretModal),
        btn('Generate password', 'secretsmanager-btn-secondary', showRandomPasswordModal),
        btn('Refresh secrets', 'secretsmanager-btn-secondary', () => refresh().catch((error) => toast(error.message, true))),
      ],
      [],
    ));
    const workbench = el('div', 'secretsmanager-workbench');
    const detail = el('div', 'secretsmanager-detail-stack'); detail.append(renderResourceTabs());
    if (!secret) detail.append(renderSecretDetail(secret));
    else {
      if (state.activeView === 'overview') detail.append(renderSecretDetail(secret));
      if (state.activeView === 'value') detail.append(renderValuePanel(secret));
      if (state.activeView === 'versions') detail.append(renderVersionsPanel(secret));
      if (state.activeView === 'rotation') detail.append(renderRotationPanel(secret));
    }
    workbench.append(renderSecretList(), detail);
    container.append(workbench);
    return container;
  }

  function render() {
    if (!root) {
      return;
    }
    renderBreadcrumbs();
    root.textContent = '';
    root.append(renderWorkbench());
    if (loadedAtEl) {
      loadedAtEl.textContent = `Loaded ${new Date().toLocaleTimeString()}`;
    }
  }

  async function refresh() {
    const data = await apiJson('/api/secretsmanager/');
    state.inventory = data;
    if (!selectedSecret() && secrets().length) {
      state.selectedSecretName = secretName(secrets()[0]);
    }
    renderSummary(data.summary || {});
    render();
  }

  function init() {
    if (!root) {
      return;
    }
    root.append(el('div', 'secretsmanager-empty', 'Loading...'));
    refresh().catch((error) => toast(error.message, true));
  }

  return { init, refresh };
})();

window.SecretsManagerConsole = SecretsManagerConsole;

if (document.getElementById('secretsmanager-console-root')) {
  SecretsManagerConsole.init();
}

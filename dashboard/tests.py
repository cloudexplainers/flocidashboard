import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound
from botocore.parsers import ResponseParserError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import Resolver404, resolve, reverse

from .actions import error_payload, error_status, handle_action_error, json_error
from .aws import (
    FlociClientFactory,
    RUNTIME_IDENTITY_SESSION_KEY,
    ResourceResult,
    cloudformation_inventory,
    ec2_inventory,
    list_resources as aws_list_resources,
    rds_inventory,
    reset_runtime_identity_override,
    set_runtime_identity_override,
)
from .services import SERVICE_PAGES, SERVICE_REGISTRY, SERVICES


ACTION_TEST_REFERENCE_GAP_BASELINE = frozenset()


class StaticJavaScriptTests(SimpleTestCase):
    def test_global_nav_brand_links_home(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'dashboard.js'
        source = script.read_text()

        self.assertIn("homeLink.className = 'global-nav-home'", source)
        self.assertIn("homeLink.href = '/'", source)
        self.assertIn("homeLink.textContent = 'Floci'", source)

    def test_collection_filter_preserves_typing_focus(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'dashboard.js'
        source = script.read_text()

        self.assertIn('onFilterTextChange(filter.value', source)
        self.assertIn('restoreCollectionFilterFocus(filter, restoreFocus', source)
        self.assertIn('let homeServiceFilterText =', source)
        self.assertIn('filterText: homeServiceFilterText', source)

    def test_service_favorites_use_heart_button_near_status(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'dashboard.js'
        source = script.read_text()

        self.assertIn("headingActions.className = 'service-card-heading-actions'", source)
        self.assertIn("favorite.textContent = isFavoriteService(service.key) ? '♥' : '♡'", source)
        self.assertIn('headingActions.append(favorite, status)', source)

    def test_global_status_center_is_wired(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'dashboard.js'
        source = script.read_text()

        self.assertIn('function classifyGlobalStatusCenter()', source)
        self.assertIn('function renderGlobalStatusCenter()', source)
        self.assertIn("button.id = 'global-status-trigger'", source)
        self.assertIn('lastHealthCheckedAt = new Date()', source)

    def test_global_nav_exposes_search_and_activity_actions(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'dashboard.js'
        source = script.read_text()

        self.assertIn("search.textContent = 'Search'", source)
        self.assertNotIn("global-nav-service-search", source)
        self.assertIn("renderGlobalNavServiceGroups(globalServiceNav, serviceMetadata, '')", source)
        self.assertIn("activity.href = '/activity/'", source)
        self.assertIn("activity.textContent = 'Activity'", source)
        self.assertIn("navActions.className = 'global-nav-actions'", source)
        self.assertIn("['Console', '/console/']", source)

    def test_service_console_exposes_resource_table_mode(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'service-console.js'
        source = script.read_text()

        self.assertIn("mode = 'cards'", source)
        self.assertIn("mode === 'table'", source)
        self.assertIn("selectAll.setAttribute('aria-label', 'Select all visible resources')", source)
        self.assertIn("link.className = 'collection-primary-link'", source)
        self.assertIn("collection-selected-bar", source)

    def test_service_summaries_support_dense_inventory(self):
        shared_script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'service-console.js'
        shared_source = shared_script.read_text()

        self.assertIn("entries.length > 8", shared_source)
        self.assertIn("'summary-grid-dense'", shared_source)
        self.assertIn("entries.forEach((entry) => container.append(renderCard(entry)))", shared_source)

    def test_service_console_syntax_highlighting_utilities(self):
        shared_script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'service-console.js'
        shared_source = shared_script.read_text()

        self.assertIn('function highlightPython(code)', shared_source)
        self.assertIn('function highlightHCL(code)', shared_source)
        self.assertIn('function highlightCLI(code)', shared_source)
        self.assertIn('function highlightJSON(code)', shared_source)
        self.assertIn('function highlightAll(root = document)', shared_source)
        self.assertIn('syntax-keyword', shared_source)
        self.assertIn('syntax-string', shared_source)
        self.assertIn('syntax-property', shared_source)

    def test_aws_cli_console_exposes_draggable_command_palette(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'console.js'
        source = script.read_text()

        self.assertIn('const COMMANDS = [', source)
        self.assertIn("category: 'Identity'", source)
        self.assertIn("command: 'aws sts get-caller-identity'", source)
        self.assertIn("command: 'aws cloudformation list-stacks'", source)
        self.assertIn('function renderCommandStrip()', source)
        self.assertIn('function enableCommandSelection()', source)
        self.assertIn("commandStrip.addEventListener('click'", source)
        self.assertNotIn('stripDragged', source)
        self.assertIn("commandStrip.addEventListener('pointerdown'", source)

    def test_aws_cli_console_uses_one_primary_output_panel(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'console.js'
        source = script.read_text()

        self.assertIn("el('summary', null, 'View raw output')", source)
        self.assertIn("renderPre('Output', JSON.stringify(data.json, null, 2))", source)
        self.assertIn("renderRawOutput(data.stdout || '')", source)
        self.assertIn("renderPre('Output', data.stdout || '')", source)
        self.assertNotIn("renderPre('stdout'", source)

    def test_s3_console_bootstraps_global_navigation(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'dashboard.js'
        source = script.read_text()

        self.assertIn('function renderGlobalNavigationForCurrentPage', source)
        self.assertIn('if (s3ConsoleRoot) {', source)
        self.assertIn('loadServiceMetadata(),', source)
        self.assertIn('renderGlobalNavigationForCurrentPage(serviceMetadata)', source)

    def test_s3_console_prioritizes_the_interactive_inventory(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 's3-console.js'
        source = script.read_text()

        self.assertIn("card.href = '#s3-console-root'", source)
        self.assertIn('value.textContent = formatBytes(summary.total_bytes || 0)', source)
        self.assertNotIn("{ label: 'ARN', render: (r)", source)
        self.assertNotIn("{ label: 'URL', render: (r)", source)
        self.assertNotIn('function renderReadOnlyInventory()', source)

    def test_iam_console_exposes_session_identity_actions(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'iam-console.js'
        source = script.read_text()

        self.assertIn("apiJson('/api/session-identity/use-user/'", source)
        self.assertIn("apiJson('/api/session-identity/assume-role/'", source)
        self.assertIn('function renderPrincipalActions(principal)', source)
        self.assertIn("btn('Use this user'", source)
        self.assertIn("btn('Assume in dashboard'", source)
        self.assertIn("btn('Get temporary credentials'", source)
        self.assertIn('function renderIdentityRecovery(error)', source)
        self.assertIn("apiJson('/api/session-identity/use-admin/'", source)
        self.assertIn("btn('Switch to floci-admin / root'", source)
        self.assertIn('iam-identity-recovery', source)
        self.assertIn('renderIdentityRecovery(error);', source)
        self.assertIn('return null;', source)
        self.assertIn('window.location.reload()', source)

    def test_iam_console_exposes_permissions_boundary_actions(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'iam-console.js'
        source = script.read_text()

        self.assertIn('function showPermissionsBoundaryModal(principal)', source)
        self.assertIn('function clearPermissionsBoundary(principal)', source)
        self.assertIn('/permissions-boundary/', source)
        self.assertIn("btn('Set boundary'", source)
        self.assertIn("btn('Clear boundary'", source)

    def test_iam_console_principal_explorer_uses_resource_table(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'iam-console.js'
        source = script.read_text()

        self.assertIn('function renderPrincipalTable(type, items)', source)
        self.assertIn("selectAll.setAttribute('aria-label', 'Select all visible principals')", source)
        self.assertIn("link.className = 'iam-principal-primary-link'", source)
        self.assertIn("'iam-selected-action-bar'", source)
        self.assertIn('Last refreshed ${refreshedAt}', source)
        self.assertNotIn('container.append(renderResourceOverview(), workbench)', source)
        self.assertIn("card.href = '#iam-console-root'", source)
        self.assertIn("const summaryTypes = ['user', 'group', 'role', 'policy', 'profile']", source)
        self.assertIn("['profile', 'Instance profiles', 'instance_profiles']", source)
        self.assertIn('function showAddRoleToInstanceProfileModal(profile)', source)
        self.assertIn("type === 'profile' ? 'Roles' : 'Policies'", source)
        self.assertIn("consoleUi.addField(details, 'Roles'", source)
        self.assertIn("if (state.selectedType !== 'profile')", source)

    def test_dashboard_credential_label_names_session_identity(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'dashboard.js'
        source = script.read_text()

        self.assertIn("data.credential_source === 'session_identity'", source)
        self.assertIn("`${data.identity_label} (${data.identity_type || 'session identity'})`", source)

    def test_static_pages_load_health_before_global_navigation(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'dashboard.js'
        source = script.read_text()

        self.assertIn('if (!refreshButton && !environmentRefreshButton && !s3ConsoleRoot) {', source)
        self.assertIn('loadStatusTiles()', source)
        self.assertIn('.then(() => loadServiceMetadata())', source)

    def test_health_and_identity_tiles_are_optional(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'dashboard.js'
        source = script.read_text()

        self.assertIn('if (health) {', source)
        self.assertIn('if (identity) {', source)
        self.assertIn('if (endpoint) {', source)
        self.assertIn('if (profile) {', source)

    def test_service_console_exposes_shared_collection_helper(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'service-console.js'
        source = script.read_text()

        self.assertIn('function renderCollection(options = {})', source)
        self.assertIn('emptyFilteredTitle', source)
        self.assertIn('onFilterTextChange(filter.value', source)
        self.assertIn('renderCollection,', source)

    def test_inventory_sections_can_use_shared_collection_helper(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'dashboard.js'
        source = script.read_text()

        self.assertIn('function renderFilterableDetailList(title, items, fields = [], options = {})', source)
        self.assertIn("filterPlaceholder: 'Find queues'", source)
        self.assertIn("filterPlaceholder: 'Find secrets'", source)
        self.assertIn("filterPlaceholder: 'Find tables'", source)
        self.assertIn("filterPlaceholder: 'Find parameters'", source)
        self.assertIn("filterPlaceholder: 'Find buckets'", source)
        self.assertIn("filterPlaceholder: 'Find instances'", source)
        self.assertIn("filterPlaceholder: 'Find VPCs'", source)
        self.assertIn("filterPlaceholder: 'Find subnets'", source)
        self.assertIn("filterPlaceholder: 'Find security groups'", source)
        self.assertIn("filterPlaceholder: 'Find user pools'", source)
        self.assertIn("filterPlaceholder: 'Find GraphQL APIs'", source)
        self.assertIn("filterPlaceholder: 'Find REST APIs'", source)
        self.assertIn("filterPlaceholder: 'Find HTTP APIs'", source)
        self.assertIn("filterPlaceholder: 'Find data sources'", source)
        self.assertIn("filterPlaceholder: 'Find resolvers'", source)
        self.assertIn("filterPlaceholder: 'Find cache clusters'", source)
        self.assertIn("filterPlaceholder: 'Find replication groups'", source)
        self.assertIn("filterPlaceholder: 'Find serverless caches'", source)
        self.assertIn("filterPlaceholder: 'Find documents'", source)
        self.assertIn("filterPlaceholder: 'Find managed instances'", source)
        self.assertIn("filterPlaceholder: 'Find commands'", source)
        self.assertIn("filterPlaceholder: 'Find stacks'", source)
        self.assertIn("filterPlaceholder: 'Find repositories'", source)
        self.assertIn("filterPlaceholder: 'Find DB instances'", source)
        self.assertIn("filterPlaceholder: 'Find DB clusters'", source)
        self.assertIn("filterPlaceholder: 'Find backup vaults'", source)
        self.assertIn("filterPlaceholder: 'Find backup jobs'", source)
        self.assertIn("filterPlaceholder: 'Find protected resources'", source)
        self.assertIn("filterPlaceholder: 'Find streams'", source)
        self.assertIn("filterPlaceholder: 'Find shards'", source)
        self.assertIn("filterPlaceholder: 'Find clusters'", source)
        self.assertIn("filterPlaceholder: 'Find hosted zones'", source)
        self.assertIn("filterPlaceholder: 'Find traffic policy instances'", source)
        self.assertIn("filterPlaceholder: 'Find servers'", source)
        self.assertIn("filterPlaceholder: 'Find workflows'", source)
        self.assertIn("filterPlaceholder: 'Find connectors'", source)
        self.assertIn("filterPlaceholder: 'Find web apps'", source)
        self.assertIn("filterPlaceholder: 'Find projects'", source)
        self.assertIn("filterPlaceholder: 'Find builds'", source)
        self.assertIn("filterPlaceholder: 'Find report groups'", source)
        self.assertIn("filterPlaceholder: 'Find deployment groups'", source)
        self.assertIn("filterPlaceholder: 'Find deployments'", source)
        self.assertIn("filterPlaceholder: 'Find pipelines'", source)
        self.assertIn("filterPlaceholder: 'Find webhooks'", source)
        self.assertIn("filterPlaceholder: 'Find users'", source)
        self.assertIn("filterPlaceholder: 'Find roles'", source)
        self.assertIn("filterPlaceholder: 'Find customer policies'", source)
        self.assertIn("filterPlaceholder: 'Find topics'", source)
        self.assertIn("filterPlaceholder: 'Find subscriptions'", source)
        self.assertIn("filterPlaceholder: 'Find identities'", source)
        self.assertIn("filterPlaceholder: 'Find email addresses'", source)
        self.assertIn("filterPlaceholder: 'Find distributions'", source)
        self.assertIn("filterPlaceholder: 'Find cache policies'", source)
        self.assertIn("filterPlaceholder: 'Find workgroups'", source)
        self.assertIn("filterPlaceholder: 'Find query executions'", source)
        self.assertIn("filterPlaceholder: 'Find databases'", source)
        self.assertIn("filterPlaceholder: 'Find user-defined functions'", source)
        self.assertIn("filterPlaceholder: 'Find registries'", source)
        self.assertIn("filterPlaceholder: 'Find security group rules'", source)
        self.assertIn("filterPlaceholder: 'Find route tables'", source)
        self.assertIn("filterPlaceholder: 'Find VPC endpoints'", source)
        self.assertIn("filterPlaceholder: 'Find AMIs'", source)
        self.assertIn("filterPlaceholder: 'Find ECS clusters'", source)
        self.assertIn("filterPlaceholder: 'Find task definitions'", source)
        self.assertIn("filterPlaceholder: 'Find EKS clusters'", source)
        self.assertIn("filterPlaceholder: 'Find node groups'", source)
        self.assertIn("filterPlaceholder: 'Find load balancers'", source)
        self.assertIn("filterPlaceholder: 'Find target groups'", source)
        self.assertIn("filterPlaceholder: 'Find delivery streams'", source)

    def test_dashboard_javascript_files_are_valid(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is required for JavaScript syntax checks.')

        static_dir = Path(__file__).resolve().parent / 'static' / 'dashboard'
        scripts = sorted(static_dir.glob('*.js'))
        self.assertTrue(scripts)

        for script in scripts:
            with self.subTest(script=script.name):
                result = subprocess.run(
                    [node, '--check', str(script)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class ActionRegistryAuditTests(SimpleTestCase):
    placeholder_pattern = re.compile(r'\{[^/{}]+\}')

    @classmethod
    def sample_action_path(cls, path):
        return cls.placeholder_pattern.sub('sample', path)

    @staticmethod
    def action_test_route_references():
        route_names = set()
        for test_file in Path(__file__).resolve().parent.glob('tests*.py'):
            text = test_file.read_text()
            route_names.update(re.findall(r"reverse\(\s*['\"]dashboard:([^'\"]+)", text))
        return route_names

    def test_action_metadata_paths_resolve_to_dashboard_routes(self):
        missing_routes = []

        for service in SERVICES:
            for action in service.actions:
                with self.subTest(service=service.key, action=action.name):
                    try:
                        match = resolve(self.sample_action_path(action.path))
                    except Resolver404:
                        missing_routes.append((service.key, action.name, action.method, action.path))
                        continue
                    self.assertEqual(match.app_name, 'dashboard')

        self.assertEqual(missing_routes, [])

    def test_action_metadata_has_endpoint_test_references(self):
        route_names = self.action_test_route_references()
        missing_references = set()

        for service in SERVICES:
            for action in service.actions:
                match = resolve(self.sample_action_path(action.path))
                if match.url_name not in route_names:
                    missing_references.add((service.key, action.name, match.url_name))

        self.assertEqual(
            missing_references,
            ACTION_TEST_REFERENCE_GAP_BASELINE,
            'Action endpoint test coverage changed. Add tests for new gaps, or remove entries from '
            'ACTION_TEST_REFERENCE_GAP_BASELINE when a historical gap is covered.',
        )

    def test_destructive_action_metadata_requires_confirm_text(self):
        missing_confirmations = [
            (service.key, action.name)
            for service in SERVICES
            for action in service.actions
            if action.safety == 'destructive' and not action.confirm
        ]

        self.assertEqual(missing_confirmations, [])


class DashboardTemplateTests(SimpleTestCase):
    def assertSharedShell(self, response):
        self.assertContains(response, 'id="global-service-nav"')
        self.assertContains(response, 'id="global-status-panel"')
        self.assertContains(response, 'id="global-status-body"')
        self.assertContains(response, 'id="global-search-overlay"')
        self.assertContains(response, 'id="global-search-input"')

    def assertCompactPageHeader(self, response):
        self.assertContains(response, 'class="page-header"')
        self.assertContains(response, 'class="page-header-actions"')
        self.assertContains(response, '>Refresh</button>')

    def test_home_page_renders_dashboard_shell(self):
        response = self.client.get(reverse('dashboard:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="root"')
        self.assertContains(response, 'app.js')
        self.assertContains(response, 'main.css')

    @patch('dashboard.views.lab_status')
    def test_page_headers_are_compact_across_primary_pages(self, status_mock):
        status_mock.return_value = {'complete': False, 'steps': {}}
        urls = [
            reverse('dashboard:legacy-environment'),
            reverse('dashboard:labs-directory'),
            reverse('dashboard:service-matrix'),
            reverse('dashboard:service-page', kwargs={'service_key': 's3'}),
            reverse('dashboard:service-labs', kwargs={'service_key': 'lambda'}),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertSharedShell(response)
                self.assertCompactPageHeader(response)
                self.assertNotContains(response, 'topbar-action-active')

    def test_environment_page_renders_diagnostics_shell(self):
        response = self.client.get(reverse('dashboard:environment'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="root"')
        self.assertContains(response, 'app.js')

    def test_legacy_environment_page_renders_diagnostics_shell(self):
        response = self.client.get(reverse('dashboard:legacy-environment'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<title>Environment - Floci Dashboard</title>', html=True)
        self.assertContains(response, '<h1 class="console-title">Environment</h1>', html=True)
        self.assertContains(response, 'id="environment-refresh"')
        self.assertContains(response, 'id="environment-state"')
        self.assertContains(response, 'id="environment-endpoint"')
        self.assertContains(response, 'id="environment-identity-arn"')
        self.assertSharedShell(response)
        self.assertContains(response, 'dashboard/dashboard.js')

    def test_settings_page_renders_runtime_configuration_shell(self):
        response = self.client.get(reverse('dashboard:settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="root"')
        self.assertContains(response, 'app.js')

    def test_legacy_settings_page_renders_runtime_configuration_shell(self):
        response = self.client.get(reverse('dashboard:legacy-settings'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<title>Settings - Floci Dashboard</title>', html=True)
        self.assertContains(response, '<h1 class="console-title">Settings</h1>', html=True)
        self.assertContains(response, 'id="settings-endpoint-url"')
        self.assertContains(response, 'id="settings-save"')
        self.assertContains(response, 'id="settings-test"')
        self.assertContains(response, 'dashboard/settings.js')
        self.assertSharedShell(response)

    def test_activity_page_renders_recent_activity_shell(self):
        response = self.client.get(reverse('dashboard:activity'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="root"')
        self.assertContains(response, 'app.js')

    def test_legacy_activity_page_renders_recent_activity_shell(self):
        response = self.client.get(reverse('dashboard:legacy-activity'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<title>Activity - Floci Dashboard</title>', html=True)
        self.assertContains(response, '<h1 class="console-title">Activity</h1>', html=True)
        self.assertContains(response, 'aria-label="Activity-enabled service workbenches"')
        self.assertContains(response, 'href="/service/apigateway/"')
        self.assertContains(response, 'href="/service/eventbridge/"')
        self.assertContains(response, 'href="/service/lambda/"')
        self.assertContains(response, 'href="/service/sqs/"')
        self.assertContains(response, 'id="activity-filter"')
        self.assertContains(response, 'id="activity-list"')
        self.assertContains(response, 'id="activity-clear"')
        self.assertContains(response, 'dashboard/service-console.js')
        self.assertContains(response, 'dashboard/activity.js')
        self.assertSharedShell(response)

    def test_console_page_renders_aws_cli_console_shell(self):
        response = self.client.get(reverse('dashboard:console'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="root"')
        self.assertContains(response, 'app.js')

    def test_legacy_console_page_renders_aws_cli_console_shell(self):
        response = self.client.get(reverse('dashboard:legacy-console'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<title>AWS CLI Console - Floci Dashboard</title>', html=True)
        self.assertContains(response, '<h1 class="console-title">AWS CLI Console</h1>', html=True)
        self.assertContains(response, 'id="aws-console-command"')
        self.assertContains(response, 'id="aws-console-run"')
        self.assertContains(response, 'id="aws-console-categories"')
        self.assertContains(response, 'id="aws-console-command-strip"')
        self.assertContains(response, 'id="aws-console-output"')
        self.assertContains(response, 'id="aws-console-history"')
        self.assertContains(response, 'dashboard/console.js')
        self.assertSharedShell(response)

    def test_service_matrix_renders_registry_coverage(self):
        response = self.client.get(reverse('dashboard:service-matrix'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<title>Service Matrix - Floci Dashboard</title>', html=True)
        self.assertContains(response, 'Service Matrix')
        self.assertContains(response, f'{len(SERVICES)} registered services')
        self.assertContains(response, 'S3')
        self.assertContains(response, '/api/s3/')
        self.assertContains(response, 'href="/service/s3/"')
        self.assertContains(response, 'Interactive Workbench')
        self.assertContains(response, 'dashboard/s3-console.js', count=0)
        self.assertSharedShell(response)
        self.assertNotContains(response, '<th scope="col">Page</th>', html=True)
        content = response.content.decode()
        self.assertLess(content.index('href="/service/iam/"'), content.index('href="/service/s3/"'))

    def test_labs_directory_lists_only_services_with_active_labs(self):
        response = self.client.get(reverse('dashboard:labs-directory'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<title>Labs - Floci Dashboard</title>', html=True)
        self.assertContains(response, '<h1 class="console-title">Labs</h1>', html=True)
        self.assertSharedShell(response)
        self.assertContains(response, '20 services with labs')
        self.assertNotContains(response, 'Learning paths')
        self.assertNotContains(response, 'Recommended starting point')
        self.assertContains(response, 'Create a local admin user')
        self.assertContains(response, 'Complete a multipart upload')
        self.assertContains(response, 'Configure SQS queue attributes and tags')
        self.assertContains(response, 'Fan out an SNS message to SQS queues')
        self.assertContains(
            response,
            'Schedule an EventBridge Scheduler message to SQS',
        )
        self.assertContains(
            response,
            'Provision S3 and SQS resources with CloudFormation',
        )
        self.assertContains(
            response,
            'Build a VPC with public and private subnets',
        )
        self.assertContains(
            response,
            'Create, invoke, and inspect a Lambda function',
        )
        self.assertContains(
            response,
            'Send an API Gateway request to Lambda',
        )
        self.assertContains(
            response,
            'Create a DynamoDB table and query items',
        )
        self.assertContains(
            response,
            'Protect local app data with KMS',
        )
        self.assertContains(
            response,
            'Read app configuration from SSM Parameter Store',
        )
        self.assertContains(
            response,
            'Create and update a Secrets Manager secret',
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 'iam'}),
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 's3'}),
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 'sqs'}),
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 'sns'}),
        )
        self.assertContains(
            response,
            reverse(
                'dashboard:service-labs',
                kwargs={'service_key': 'scheduler'},
            ),
        )
        self.assertContains(
            response,
            reverse(
                'dashboard:service-labs',
                kwargs={'service_key': 'cloudformation'},
            ),
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 'ec2'}),
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 'lambda'}),
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 'apigateway'}),
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 'dynamodb'}),
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 'kms'}),
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 'ssm'}),
        )
        self.assertContains(
            response,
            reverse('dashboard:service-labs', kwargs={'service_key': 'secretsmanager'}),
        )
        content = response.content.decode()
        self.assertLess(content.index('href="/service/iam/"'), content.index('href="/service/s3/"'))
        self.assertLess(content.index('href="/service/s3/"'), content.index('href="/service/sqs/"'))
        self.assertLess(content.index('href="/service/sqs/"'), content.index('href="/service/sns/"'))

    def test_all_service_pages_render(self):
        for key, service in SERVICE_PAGES.items():
            with self.subTest(service=key):
                response = self.client.get(reverse('dashboard:service-page', kwargs={'service_key': key}))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'<title>{service["title"]} - Floci Dashboard</title>', html=True)
                self.assertContains(response, f'<h1 class="console-title">{service["title"]}</h1>', html=True)
                self.assertContains(response, service['eyebrow'])
                self.assertSharedShell(response)
                self.assertContains(response, 'dashboard/styles.css')
                self.assertContains(response, 'dashboard/dashboard.js')
                self.assertContains(response, f'id="{key}-loaded-at"')
                self.assertContains(response, f'id="{key}-summary"')
                definition = SERVICE_REGISTRY[key]
                if definition.console_js:
                    self.assertContains(response, f'id="{key}-console-root"')
                    self.assertNotContains(response, f'id="{key}-grid"')
                    self.assertContains(response, 'dashboard/service-console.js')
                    self.assertContains(response, definition.console_js)
                else:
                    self.assertContains(response, f'id="{key}-grid"')

    def test_unknown_service_page_404s(self):
        response = self.client.get(reverse('dashboard:service-page', kwargs={'service_key': 'not-a-service'}))

        self.assertEqual(response.status_code, 404)


class ActionErrorTests(SimpleTestCase):
    def test_value_error_payload_includes_operation_context(self):
        response = handle_action_error(ValueError('Name is required'), service='sqs', operation='create_queue')

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'Name is required')
        self.assertEqual(payload['service'], 'sqs')
        self.assertEqual(payload['operation'], 'create_queue')
        self.assertEqual(payload['operation_label'], 'Create queue')
        self.assertEqual(payload['type'], 'ValueError')
        self.assertEqual(payload['status'], 400)

    def test_client_error_status_mapping_matches_common_aws_shapes(self):
        missing = ClientError({'Error': {'Code': 'ResourceNotFoundException', 'Message': 'Missing'}}, 'DescribeThing')
        invalid = ClientError({'Error': {'Code': 'ValidationException', 'Message': 'Bad input'}}, 'CreateThing')
        denied = ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': 'Nope'}}, 'CreateThing')
        throttled = ClientError({'Error': {'Code': 'ThrottlingException', 'Message': 'Slow down'}}, 'CreateThing')

        self.assertEqual(error_status(missing), 404)
        self.assertEqual(error_status(invalid), 400)
        self.assertEqual(error_status(denied), 403)
        self.assertEqual(error_status(throttled), 429)

    def test_client_error_payload_prefers_aws_error_message_and_code(self):
        exc = ClientError({'Error': {'Code': 'ValidationException', 'Message': 'Bad input'}}, 'CreateThing')

        payload = error_payload(exc, service='glue', operation='create_table')

        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'Bad input')
        self.assertEqual(payload['code'], 'ValidationException')
        self.assertEqual(payload['operation_label'], 'Create table')

    def test_json_error_uses_normalized_payload_shape(self):
        response = json_error('Invalid key', service='s3', operation='upload_object')

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['error'], 'Invalid key')
        self.assertEqual(payload['service'], 's3')
        self.assertEqual(payload['operation'], 'upload_object')
        self.assertEqual(payload['operation_label'], 'Upload object')
        self.assertEqual(payload['status'], 400)


class DashboardSettingsApiTests(TestCase):
    def factory(self, endpoint='http://localhost:4566', source='settings'):
        factory = MagicMock()
        factory.endpoint_url = endpoint
        factory.endpoint_source = source
        factory.region = 'us-east-1'
        factory.credential_context.return_value = {
            'credential_source': 'local_default',
            'endpoint_source': source,
            'has_env_credentials': False,
            'profile': None,
            'profile_source': None,
            'region_source': 'settings',
        }
        factory.health.return_value = {
            'ok': True,
            'url': f'{endpoint}/_floci/health',
            'data': {'version': '1.5.31', 'edition': 'Community'},
        }
        factory.identity.return_value = {
            'account': '000000000000',
            'arn': 'arn:aws:iam::000000000000:user/test',
            'user_id': 'test',
        }
        factory.local_identity_hint.return_value = None
        return factory

    @override_settings(FLOCI_AWS_ENDPOINT_URL='http://localhost:4566')
    @patch('dashboard.settings_views.FlociClientFactory')
    def test_settings_detail_returns_effective_configuration(self, factory_mock):
        factory_mock.return_value = self.factory()

        with patch.dict(os.environ, {
            'FLOCI_AWS_ENDPOINT_URL': 'http://localhost:4566',
            'AWS_ENDPOINT_URL': 'http://localhost:4566',
        }):
            response = self.client.get(reverse('dashboard:settings-detail'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['endpoint_url'], 'http://localhost:4566')
        self.assertEqual(payload['endpoint_source'], 'settings')
        self.assertEqual(payload['default_endpoint_url'], 'http://localhost:4566')
        self.assertEqual(payload['region'], 'us-east-1')

    def test_endpoint_save_rejects_invalid_endpoint(self):
        response = self.client.post(
            reverse('dashboard:settings-endpoint'),
            data=json.dumps({'endpoint_url': 'not-a-url'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('valid http or https URL', response.json()['error'])

    @patch('dashboard.settings_views.FlociClientFactory')
    def test_endpoint_save_stores_session_override(self, factory_mock):
        endpoint = 'http://127.0.0.1:4566'
        factory_mock.return_value = self.factory(endpoint=endpoint, source='runtime_override')

        response = self.client.post(
            reverse('dashboard:settings-endpoint'),
            data=json.dumps({'endpoint_url': f'{endpoint}/'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['saved'])
        self.assertEqual(payload['endpoint_url'], endpoint)
        self.assertEqual(payload['runtime_endpoint_url'], endpoint)
        self.assertEqual(self.client.session['floci_runtime_endpoint_url'], endpoint)

    @patch('dashboard.settings_views.FlociClientFactory')
    def test_endpoint_reset_clears_session_override(self, factory_mock):
        session = self.client.session
        session['floci_runtime_endpoint_url'] = 'http://127.0.0.1:4566'
        session.save()
        factory_mock.return_value = self.factory()

        response = self.client.delete(reverse('dashboard:settings-endpoint-reset'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['reset'])
        self.assertNotIn('floci_runtime_endpoint_url', self.client.session)

    @patch('dashboard.settings_views.FlociClientFactory')
    def test_test_connection_returns_health_and_identity(self, factory_mock):
        endpoint = 'http://localhost:4566'
        factory_mock.return_value = self.factory(endpoint=endpoint, source='submitted')

        response = self.client.post(
            reverse('dashboard:settings-test-connection'),
            data=json.dumps({'endpoint_url': endpoint}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['endpoint_url'], endpoint)
        self.assertEqual(payload['endpoint_source'], 'submitted')
        self.assertTrue(payload['health']['ok'])
        self.assertTrue(payload['identity_resolved'])
        self.assertEqual(payload['identity']['account'], '000000000000')

    @patch('dashboard.settings_views.FlociClientFactory')
    def test_settings_floci_reset_calls_factory_reset_state(self, factory_mock):
        factory = self.factory()
        factory.reset_state.return_value = {'ok': True, 'data': {'status': 'OK'}}
        factory_mock.return_value = factory

        response = self.client.post(reverse('dashboard:settings-floci-reset'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['reset_result'], {'ok': True, 'data': {'status': 'OK'}})
        factory.reset_state.assert_called_once()

    @patch('dashboard.views.FlociClientFactory')
    def test_init_lifecycle_endpoint_returns_payload(self, factory_mock):
        factory = self.factory()
        factory.init_status.return_value = {
            'ok': True,
            'data': {'completed': {'boot': True, 'ready': True}, 'scripts': {}},
        }
        factory_mock.return_value = factory

        response = self.client.get(reverse('dashboard:init-lifecycle'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertTrue(payload['data']['completed']['ready'])


class RuntimeIdentityFactoryTests(TestCase):
    def test_runtime_identity_takes_precedence_over_environment_credentials(self):
        token = set_runtime_identity_override({
            'type': 'user',
            'label': 'charlie',
            'access_key_id': 'AKIASESSION',
            'secret_access_key': 'session-secret',
            'session_token': 'session-token',
            'expires_at': '2026-07-09T00:00:00+00:00',
        })
        try:
            with patch.dict(os.environ, {
                'AWS_ACCESS_KEY_ID': 'env-key',
                'AWS_SECRET_ACCESS_KEY': 'env-secret',
                'AWS_ENDPOINT_URL': 'http://localhost:4566',
            }, clear=False):
                factory = FlociClientFactory()
        finally:
            reset_runtime_identity_override(token)

        self.assertEqual(factory.credential_source, 'session_identity')
        self.assertEqual(factory.access_key_id, 'AKIASESSION')
        self.assertEqual(factory.secret_access_key, 'session-secret')
        self.assertEqual(factory.session_token, 'session-token')
        self.assertEqual(factory.identity_type, 'user')
        self.assertEqual(factory.identity_label, 'charlie')
        self.assertEqual(factory.credential_context()['identity_expires_at'], '2026-07-09T00:00:00+00:00')


class DashboardIdentityApiTests(TestCase):
    def identity_factory(self):
        factory = MagicMock(spec=FlociClientFactory)
        factory.endpoint_url = 'http://localhost:4566'
        factory.region = 'us-east-1'
        factory.credential_context.return_value = {
            'credential_source': 'session_identity',
            'endpoint_source': 'settings',
            'has_env_credentials': True,
            'identity_expires_at': None,
            'identity_label': 'charlie',
            'identity_type': 'user',
            'profile': None,
            'profile_source': None,
            'region_source': 'settings',
        }
        factory.identity.return_value = {
            'account': '000000000000',
            'arn': 'arn:aws:iam::000000000000:user/charlie',
            'user_id': 'charlie',
        }
        factory.local_identity_hint.return_value = None
        return factory

    @patch('dashboard.identity_views.FlociClientFactory')
    def test_identity_use_user_stores_session_credentials(self, factory_mock):
        factory = self.identity_factory()
        iam = MagicMock()
        iam.list_access_keys.return_value = {'AccessKeyMetadata': [{'AccessKeyId': 'OLDKEY'}]}
        iam.create_access_key.return_value = {
            'AccessKey': {
                'AccessKeyId': 'AKIACHARLIE',
                'SecretAccessKey': 'charlie-secret',
            },
        }
        factory.client.return_value = iam
        factory_mock.return_value = factory

        response = self.client.post(
            reverse('dashboard:session-identity-use-user'),
            data=json.dumps({'user_name': 'charlie'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        identity = self.client.session[RUNTIME_IDENTITY_SESSION_KEY]
        self.assertEqual(identity['type'], 'user')
        self.assertEqual(identity['label'], 'charlie')
        self.assertEqual(identity['access_key_id'], 'AKIACHARLIE')
        self.assertEqual(identity['secret_access_key'], 'charlie-secret')
        iam.put_user_policy.assert_called_once()
        iam.delete_access_key.assert_not_called()
        iam.create_access_key.assert_called_once_with(UserName='charlie')

    @patch('dashboard.identity_views.FlociClientFactory')
    def test_identity_use_user_can_replace_explicit_access_key(self, factory_mock):
        factory = self.identity_factory()
        iam = MagicMock()
        iam.create_access_key.return_value = {
            'AccessKey': {
                'AccessKeyId': 'AKIACHARLIE',
                'SecretAccessKey': 'charlie-secret',
            },
        }
        factory.client.return_value = iam
        factory_mock.return_value = factory

        response = self.client.post(
            reverse('dashboard:session-identity-use-user'),
            data=json.dumps({'user_name': 'charlie', 'replace_access_key_id': 'OLDKEY'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        iam.delete_access_key.assert_called_once_with(UserName='charlie', AccessKeyId='OLDKEY')
        iam.create_access_key.assert_called_once_with(UserName='charlie')

    @patch('dashboard.identity_views.FlociClientFactory')
    def test_identity_assume_role_stores_session_token(self, factory_mock):
        session = self.client.session
        session[RUNTIME_IDENTITY_SESSION_KEY] = {
            'type': 'user',
            'label': 'charlie',
            'access_key_id': 'AKIACHARLIE',
            'secret_access_key': 'charlie-secret',
        }
        session.save()
        factory = self.identity_factory()
        sts = MagicMock()
        sts.assume_role.return_value = {
            'Credentials': {
                'AccessKeyId': 'ASIAROLE',
                'SecretAccessKey': 'role-secret',
                'SessionToken': 'role-token',
                'Expiration': '2026-07-09T00:00:00+00:00',
            },
            'AssumedRoleUser': {
                'Arn': 'arn:aws:sts::000000000000:assumed-role/CharlieSqsReadRole/floci-session',
            },
        }
        factory.client.return_value = sts
        factory_mock.return_value = factory

        response = self.client.post(
            reverse('dashboard:session-identity-assume-role'),
            data=json.dumps({'role_name': 'CharlieSqsReadRole', 'session_name': 'floci-session'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        identity = self.client.session[RUNTIME_IDENTITY_SESSION_KEY]
        self.assertEqual(identity['type'], 'assumed_role')
        self.assertEqual(identity['label'], 'CharlieSqsReadRole/floci-session')
        self.assertEqual(identity['access_key_id'], 'ASIAROLE')
        self.assertEqual(identity['session_token'], 'role-token')
        sts.assume_role.assert_called_once_with(
            RoleArn='arn:aws:iam::000000000000:role/CharlieSqsReadRole',
            RoleSessionName='floci-session',
        )

    def test_identity_clear_removes_session_identity(self):
        session = self.client.session
        session[RUNTIME_IDENTITY_SESSION_KEY] = {
            'type': 'user',
            'label': 'charlie',
            'access_key_id': 'AKIACHARLIE',
            'secret_access_key': 'charlie-secret',
        }
        session.save()

        response = self.client.post(reverse('dashboard:session-identity-clear'))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(RUNTIME_IDENTITY_SESSION_KEY, self.client.session)


class AwsCliConsoleApiTests(TestCase):
    def factory(self):
        factory = MagicMock(spec=FlociClientFactory)
        factory.endpoint_url = 'http://localhost:4566'
        factory.region = 'us-east-1'
        factory.profile = None
        factory.access_key_id = 'test'
        factory.secret_access_key = 'test'
        factory.session_token = None
        factory.credential_source = 'local_default'
        factory.identity_expires_at = None
        factory.identity_label = None
        factory.identity_type = None
        return factory

    @patch('dashboard.console_views.subprocess.run')
    @patch('dashboard.console_views.shutil.which')
    @patch('dashboard.console_views.FlociClientFactory')
    def test_console_run_executes_aws_command_with_local_endpoint(
        self,
        factory_mock,
        which_mock,
        run_mock,
    ):
        factory_mock.return_value = self.factory()
        which_mock.return_value = '/usr/local/bin/aws'
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"UserId":"test"}\n',
            stderr='',
        )

        response = self.client.post(
            reverse('dashboard:console-run'),
            data=json.dumps({'command': 'aws sts get-caller-identity'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['json'], {'UserId': 'test'})
        args = run_mock.call_args.args[0]
        self.assertEqual(args[:3], ['/usr/local/bin/aws', '--endpoint-url', 'http://localhost:4566'])
        self.assertIn('sts', args)
        env = run_mock.call_args.kwargs['env']
        self.assertEqual(env['AWS_ACCESS_KEY_ID'], 'test')
        self.assertEqual(env['AWS_SECRET_ACCESS_KEY'], 'test')
        self.assertEqual(env['AWS_ENDPOINT_URL'], 'http://localhost:4566')

    @patch('dashboard.console_views.subprocess.run')
    @patch('dashboard.console_views.shutil.which')
    @patch('dashboard.console_views.FlociClientFactory')
    def test_console_run_allows_submitted_local_endpoint(
        self,
        factory_mock,
        which_mock,
        run_mock,
    ):
        factory_mock.return_value = self.factory()
        which_mock.return_value = '/usr/local/bin/aws'
        run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')

        response = self.client.post(
            reverse('dashboard:console-run'),
            data=json.dumps({'command': 'aws --endpoint-url http://127.0.0.1:4566 s3 ls'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        args = run_mock.call_args.args[0]
        self.assertEqual(args[:4], ['/usr/local/bin/aws', '--endpoint-url', 'http://127.0.0.1:4566', 's3'])

    @patch('dashboard.console_views.subprocess.run')
    @patch('dashboard.console_views.shutil.which')
    @patch('dashboard.console_views.FlociClientFactory')
    def test_console_run_uses_session_token_credentials(
        self,
        factory_mock,
        which_mock,
        run_mock,
    ):
        factory = self.factory()
        factory.access_key_id = 'ASIAROLE'
        factory.secret_access_key = 'role-secret'
        factory.session_token = 'role-token'
        factory.credential_source = 'session_identity'
        factory.identity_label = 'CharlieSqsReadRole/floci-session'
        factory.identity_type = 'assumed_role'
        factory_mock.return_value = factory
        which_mock.return_value = '/usr/local/bin/aws'
        run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')

        response = self.client.post(
            reverse('dashboard:console-run'),
            data=json.dumps({'command': 'aws sqs list-queues'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        env = run_mock.call_args.kwargs['env']
        self.assertEqual(env['AWS_ACCESS_KEY_ID'], 'ASIAROLE')
        self.assertEqual(env['AWS_SECRET_ACCESS_KEY'], 'role-secret')
        self.assertEqual(env['AWS_SESSION_TOKEN'], 'role-token')
        payload = response.json()
        self.assertEqual(payload['identity_label'], 'CharlieSqsReadRole/floci-session')
        self.assertEqual(payload['identity_type'], 'assumed_role')

    def test_console_run_rejects_non_aws_commands(self):
        response = self.client.post(
            reverse('dashboard:console-run'),
            data=json.dumps({'command': 'ls -la'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('Only commands starting with aws', response.json()['error'])

    def test_console_run_rejects_shell_operators(self):
        response = self.client.post(
            reverse('dashboard:console-run'),
            data=json.dumps({'command': 'aws s3 ls; rm -rf /'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('Shell operators', response.json()['error'])

    def test_console_run_rejects_file_inputs(self):
        response = self.client.post(
            reverse('dashboard:console-run'),
            data=json.dumps({'command': 'aws iam create-policy --policy-document file://policy.json'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('file://', response.json()['error'])

    def test_console_run_rejects_profile_switches(self):
        response = self.client.post(
            reverse('dashboard:console-run'),
            data=json.dumps({'command': 'aws --profile floci-admin s3 ls'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('--profile is not supported', response.json()['error'])

    @patch('dashboard.console_views.FlociClientFactory')
    def test_console_run_requires_confirmation_for_destructive_commands(self, factory_mock):
        factory_mock.return_value = self.factory()

        response = self.client.post(
            reverse('dashboard:console-run'),
            data=json.dumps({'command': 'aws s3 rb s3://demo-bucket'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 409)
        payload = response.json()
        self.assertTrue(payload['requires_confirmation'])
        self.assertTrue(payload['destructive'])

    def test_console_run_rejects_external_endpoint(self):
        response = self.client.post(
            reverse('dashboard:console-run'),
            data=json.dumps({'command': 'aws --endpoint-url https://example.com s3 ls'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('Only local AWS endpoint', response.json()['error'])


class ServiceRegistryApiTests(SimpleTestCase):
    def test_services_api_exposes_registry_metadata(self):
        response = self.client.get(reverse('dashboard:services'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        services = {service['key']: service for service in payload['services']}
        self.assertEqual(len(services), len(SERVICES))
        self.assertIn('interactive_workbench', payload['maturity_levels'])
        self.assertEqual(services['s3']['maturity'], 'interactive_workbench')
        self.assertTrue(services['s3']['shared_console'])
        self.assertEqual(services['s3']['console_js'], 'dashboard/s3-console.js')
        s3_actions = {action['name']: action for action in services['s3']['actions']}
        self.assertEqual(s3_actions['create_bucket']['method'], 'POST')
        self.assertEqual(s3_actions['delete_bucket']['safety'], 'destructive')
        self.assertEqual(s3_actions['upload_object']['fields'][1]['field_type'], 'file')
        self.assertEqual(s3_actions['put_website']['path'], '/api/s3/buckets/{bucket}/website/')
        self.assertEqual(s3_actions['delete_website']['safety'], 'destructive')
        self.assertEqual(services['sqs']['maturity'], 'interactive_workbench')
        self.assertEqual(services['sqs']['console_js'], 'dashboard/sqs-console.js')
        sqs_actions = {action['name']: action for action in services['sqs']['actions']}
        self.assertEqual(sqs_actions['send_message']['method'], 'POST')
        self.assertEqual(sqs_actions['receive_messages']['safety'], 'safe')
        self.assertEqual(sqs_actions['purge_queue']['safety'], 'destructive')
        self.assertEqual(services['sns']['maturity'], 'interactive_workbench')
        self.assertTrue(services['sns']['shared_console'])
        self.assertEqual(services['sns']['console_js'], 'dashboard/sns-console.js')
        sns_actions = {action['name']: action for action in services['sns']['actions']}
        self.assertEqual(sns_actions['publish_message']['method'], 'POST')
        self.assertEqual(sns_actions['publish_message']['fields'][1]['field_type'], 'textarea')
        self.assertEqual(services['lambda']['maturity'], 'interactive_workbench')
        self.assertEqual(services['lambda']['console_js'], 'dashboard/lambda-console.js')
        lambda_actions = {action['name']: action for action in services['lambda']['actions']}
        self.assertEqual(lambda_actions['invoke_function']['method'], 'POST')
        self.assertEqual(lambda_actions['invoke_function']['kind'], 'execute')
        self.assertEqual(services['iam']['maturity'], 'interactive_workbench')
        self.assertEqual(services['iam']['console_js'], 'dashboard/iam-console.js')
        iam_actions = {action['name']: action for action in services['iam']['actions']}
        self.assertEqual(iam_actions['create_user']['kind'], 'create')
        self.assertEqual(iam_actions['create_user']['path'], '/api/iam/users/')
        self.assertEqual(iam_actions['create_role']['path'], '/api/iam/roles/')
        self.assertEqual(iam_actions['delete_user']['safety'], 'destructive')
        self.assertEqual(iam_actions['simulate_principal_policy']['safety'], 'safe')
        self.assertEqual(iam_actions['create_access_key']['kind'], 'create')
        self.assertEqual(iam_actions['assume_role']['kind'], 'execute')
        self.assertEqual(iam_actions['delete_access_key']['safety'], 'destructive')
        self.assertEqual(iam_actions['put_permissions_boundary']['path'], '/api/iam/principals/{principal_type}/{principal}/permissions-boundary/')
        self.assertEqual(iam_actions['delete_permissions_boundary']['safety'], 'destructive')
        self.assertEqual(iam_actions['put_inline_policy']['fields'][1]['field_type'], 'textarea')
        self.assertEqual(services['dynamodb']['maturity'], 'interactive_workbench')
        self.assertEqual(services['dynamodb']['console_js'], 'dashboard/dynamodb-console.js')
        dynamodb_actions = {action['name']: action for action in services['dynamodb']['actions']}
        self.assertEqual(dynamodb_actions['scan_table']['safety'], 'safe')
        self.assertEqual(dynamodb_actions['execute_select_statement']['fields'][0]['field_type'], 'textarea')
        self.assertEqual(services['cloudwatch']['maturity'], 'interactive_workbench')
        self.assertEqual(services['cloudwatch']['console_js'], 'dashboard/cloudwatch-console.js')
        cloudwatch_actions = {action['name']: action for action in services['cloudwatch']['actions']}
        self.assertEqual(cloudwatch_actions['list_log_streams']['safety'], 'safe')
        self.assertEqual(cloudwatch_actions['get_log_events']['fields'][1]['name'], 'log_stream_name')
        self.assertEqual(cloudwatch_actions['start_logs_insights_query']['fields'][1]['field_type'], 'textarea')
        self.assertEqual(cloudwatch_actions['get_logs_insights_query_results']['fields'][0]['name'], 'query_id')
        self.assertEqual(services['stepfunctions']['maturity'], 'interactive_workbench')
        self.assertEqual(services['stepfunctions']['console_js'], 'dashboard/stepfunctions-console.js')
        stepfunctions_actions = {action['name']: action for action in services['stepfunctions']['actions']}
        self.assertEqual(stepfunctions_actions['start_execution']['kind'], 'execute')
        self.assertEqual(stepfunctions_actions['start_execution']['fields'][2]['field_type'], 'textarea')
        self.assertEqual(stepfunctions_actions['stop_execution']['safety'], 'destructive')
        self.assertEqual(services['cloudformation']['maturity'], 'interactive_workbench')
        self.assertEqual(services['cloudformation']['console_js'], 'dashboard/cloudformation-console.js')
        cloudformation_actions = {action['name']: action for action in services['cloudformation']['actions']}
        self.assertEqual(cloudformation_actions['validate_template']['safety'], 'safe')
        self.assertEqual(cloudformation_actions['create_stack']['fields'][1]['field_type'], 'textarea')
        self.assertEqual(cloudformation_actions['delete_stack']['safety'], 'destructive')
        self.assertEqual(cloudformation_actions['execute_change_set']['kind'], 'execute')
        self.assertEqual(services['cognito']['maturity'], 'interactive_workbench')
        self.assertEqual(services['cognito']['console_js'], 'dashboard/cognito-console.js')
        cognito_actions = {action['name']: action for action in services['cognito']['actions']}
        self.assertEqual(cognito_actions['create_user_pool']['kind'], 'create')
        self.assertEqual(cognito_actions['admin_create_user']['fields'][2]['field_type'], 'object')
        self.assertEqual(cognito_actions['admin_delete_user']['safety'], 'destructive')
        self.assertEqual(cognito_actions['initiate_auth']['safety'], 'safe')
        self.assertEqual(cognito_actions['oauth_client_credentials']['kind'], 'execute')
        self.assertEqual(services['eventbridge']['maturity'], 'interactive_workbench')
        self.assertEqual(services['eventbridge']['console_js'], 'dashboard/eventbridge-console.js')
        eventbridge_actions = {action['name']: action for action in services['eventbridge']['actions']}
        self.assertEqual(eventbridge_actions['put_event']['method'], 'POST')
        self.assertEqual(eventbridge_actions['put_event']['kind'], 'execute')
        self.assertEqual(eventbridge_actions['put_event']['fields'][3]['field_type'], 'textarea')
        self.assertEqual(services['apigateway']['maturity'], 'interactive_workbench')
        self.assertEqual(services['apigateway']['console_js'], 'dashboard/apigateway-console.js')
        apigateway_actions = {action['name']: action for action in services['apigateway']['actions']}
        self.assertEqual(apigateway_actions['test_request']['method'], 'POST')
        self.assertEqual(apigateway_actions['test_request']['safety'], 'safe')
        self.assertEqual(apigateway_actions['test_request']['fields'][7]['field_type'], 'textarea')
        self.assertEqual(services['kinesis']['maturity'], 'interactive_workbench')
        self.assertEqual(services['kinesis']['console_js'], 'dashboard/kinesis-console.js')
        kinesis_actions = {action['name']: action for action in services['kinesis']['actions']}
        self.assertEqual(kinesis_actions['create_stream']['kind'], 'create')
        self.assertEqual(kinesis_actions['put_record']['fields'][1]['field_type'], 'textarea')
        self.assertEqual(kinesis_actions['get_records']['safety'], 'safe')
        self.assertEqual(services['secretsmanager']['maturity'], 'interactive_workbench')
        self.assertEqual(services['secretsmanager']['console_js'], 'dashboard/secretsmanager-console.js')
        secrets_actions = {action['name']: action for action in services['secretsmanager']['actions']}
        self.assertEqual(secrets_actions['create_secret']['fields'][1]['field_type'], 'textarea')
        self.assertEqual(secrets_actions['get_secret_value']['safety'], 'safe')
        self.assertEqual(secrets_actions['delete_secret']['safety'], 'destructive')
        self.assertEqual(services['ssm']['maturity'], 'interactive_workbench')
        self.assertEqual(services['ssm']['console_js'], 'dashboard/ssm-console.js')
        ssm_actions = {action['name']: action for action in services['ssm']['actions']}
        self.assertEqual(ssm_actions['put_parameter']['fields'][2]['field_type'], 'textarea')
        self.assertEqual(ssm_actions['get_parameter']['safety'], 'safe')
        self.assertEqual(ssm_actions['delete_parameter']['safety'], 'destructive')
        self.assertEqual(services['ec2']['maturity'], 'interactive_workbench')
        self.assertEqual(services['ec2']['console_js'], 'dashboard/ec2-console.js')
        ec2_actions = {action['name']: action for action in services['ec2']['actions']}
        self.assertEqual(ec2_actions['run_instances']['kind'], 'create')
        self.assertEqual(ec2_actions['run_instances']['fields'][5]['field_type'], 'textarea')
        self.assertEqual(ec2_actions['terminate_instance']['safety'], 'destructive')
        self.assertEqual(ec2_actions['import_key_pair']['fields'][1]['field_type'], 'textarea')
        self.assertEqual(services['rds']['maturity'], 'interactive_workbench')
        self.assertEqual(services['rds']['console_js'], 'dashboard/rds-console.js')
        rds_actions = {action['name']: action for action in services['rds']['actions']}
        self.assertEqual(rds_actions['create_db_instance']['kind'], 'create')
        self.assertEqual(rds_actions['create_db_instance']['fields'][4]['field_type'], 'number')
        self.assertEqual(rds_actions['reboot_db_instance']['kind'], 'execute')
        self.assertEqual(rds_actions['delete_db_instance']['safety'], 'destructive')
        self.assertEqual(rds_actions['create_db_parameter_group']['fields'][2]['name'], 'description')
        self.assertEqual(services['autoscaling']['maturity'], 'interactive_workbench')
        self.assertEqual(services['autoscaling']['console_js'], 'dashboard/autoscaling-console.js')
        autoscaling_actions = {action['name']: action for action in services['autoscaling']['actions']}
        self.assertEqual(autoscaling_actions['create_launch_configuration']['fields'][5]['field_type'], 'textarea')
        self.assertEqual(autoscaling_actions['create_auto_scaling_group']['fields'][2]['field_type'], 'number')
        self.assertEqual(autoscaling_actions['set_desired_capacity']['kind'], 'execute')
        self.assertEqual(autoscaling_actions['detach_instances']['safety'], 'destructive')
        self.assertEqual(autoscaling_actions['terminate_instance']['safety'], 'destructive')
        self.assertEqual(services['elasticloadbalancing']['maturity'], 'interactive_workbench')
        self.assertEqual(services['elasticloadbalancing']['console_js'], 'dashboard/elasticloadbalancing-console.js')
        elbv2_actions = {action['name']: action for action in services['elasticloadbalancing']['actions']}
        self.assertEqual(elbv2_actions['create_load_balancer']['kind'], 'create')
        self.assertEqual(elbv2_actions['create_target_group']['fields'][2]['field_type'], 'number')
        self.assertEqual(elbv2_actions['register_targets']['fields'][0]['field_type'], 'array')
        self.assertEqual(elbv2_actions['delete_load_balancer']['safety'], 'destructive')
        self.assertEqual(elbv2_actions['delete_rule']['safety'], 'destructive')
        self.assertEqual(services['eks']['maturity'], 'interactive_workbench')
        self.assertEqual(services['eks']['console_js'], 'dashboard/eks-console.js')
        eks_actions = {action['name']: action for action in services['eks']['actions']}
        self.assertEqual(eks_actions['create_cluster']['fields'][1]['name'], 'role_arn')
        self.assertEqual(eks_actions['delete_cluster']['safety'], 'destructive')
        self.assertEqual(eks_actions['list_tags_for_resource']['safety'], 'safe')
        self.assertEqual(services['elasticache']['maturity'], 'interactive_workbench')
        self.assertEqual(services['elasticache']['console_js'], 'dashboard/elasticache-console.js')
        elasticache_actions = {action['name']: action for action in services['elasticache']['actions']}
        self.assertEqual(elasticache_actions['create_replication_group']['kind'], 'create')
        self.assertEqual(elasticache_actions['delete_replication_group']['safety'], 'destructive')
        self.assertEqual(elasticache_actions['validate_iam_auth_token']['safety'], 'safe')
        self.assertIn('max-age=60', response.headers['Cache-Control'])
        self.assertIn('public', response.headers['Cache-Control'])
        self.assertEqual(services['opensearch']['maturity'], 'interactive_workbench')
        self.assertEqual(services['opensearch']['console_js'], 'dashboard/opensearch-console.js')
        opensearch_actions = {action['name']: action for action in services['opensearch']['actions']}
        self.assertEqual(opensearch_actions['create_domain']['kind'], 'create')
        self.assertEqual(opensearch_actions['delete_domain']['safety'], 'destructive')
        self.assertEqual(opensearch_actions['list_versions']['safety'], 'safe')
        self.assertEqual(services['athena']['maturity'], 'interactive_workbench')
        self.assertEqual(services['athena']['console_js'], 'dashboard/athena-console.js')
        athena_actions = {action['name']: action for action in services['athena']['actions']}
        self.assertEqual(athena_actions['start_query_execution']['kind'], 'execute')
        self.assertEqual(athena_actions['get_query_results']['safety'], 'safe')
        self.assertEqual(athena_actions['stop_query_execution']['safety'], 'destructive')
        self.assertEqual(services['lambda']['api_path'], '/api/lambda/')
        self.assertEqual(services['cloudfront']['maturity'], 'interactive_workbench')
        self.assertEqual(services['cloudfront']['api_path'], '/api/cloudfront/')
        self.assertEqual(services['cloudfront']['console_js'], 'dashboard/cloudfront-console.js')
        cloudfront_actions = {action['name']: action for action in services['cloudfront']['actions']}
        self.assertEqual(cloudfront_actions['create_distribution']['kind'], 'create')
        self.assertEqual(cloudfront_actions['create_invalidation']['kind'], 'execute')
        self.assertEqual(cloudfront_actions['delete_distribution']['safety'], 'destructive')
        self.assertEqual(services['cloudmap']['title'], 'Cloud Map')
        self.assertEqual(services['cloudmap']['api_path'], '/api/cloudmap/')
        self.assertEqual(services['cloudmap']['maturity'], 'interactive_workbench')
        self.assertEqual(services['config']['title'], 'AWS Config')
        self.assertEqual(services['config']['api_path'], '/api/config/')

    @patch('dashboard.views.FlociClientFactory.identity')
    def test_identity_api_includes_credential_context(self, identity):
        identity.return_value = {
            'account': '000000000000',
            'arn': 'arn:aws:iam::000000000000:user/test',
            'user_id': 'AIDAEXAMPLE',
        }

        with patch.dict(os.environ, {
            'AWS_ACCESS_KEY_ID': 'test',
            'AWS_SECRET_ACCESS_KEY': 'test',
        }, clear=True):
            response = self.client.get(reverse('dashboard:identity'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['credential_source'], 'environment')
        self.assertTrue(payload['has_env_credentials'])
        self.assertIsNone(payload['profile'])
        self.assertIsNone(payload['profile_source'])

    @patch('dashboard.views.FlociClientFactory.identity')
    def test_identity_api_uses_test_identity_hint_when_sts_is_unresolved(self, identity):
        identity.side_effect = NoCredentialsError()

        with patch.dict(os.environ, {
            'AWS_ACCESS_KEY_ID': 'test',
            'AWS_SECRET_ACCESS_KEY': 'test',
        }, clear=True):
            response = self.client.get(reverse('dashboard:identity'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['identity']['user_id'], 'test')
        self.assertEqual(payload['identity']['arn'], 'test (local credentials)')
        self.assertFalse(payload['identity_resolved'])
        self.assertIn('Unable to locate credentials', payload['identity_error'])

    def test_service_pages_are_derived_from_registry(self):
        self.assertEqual(set(SERVICE_PAGES), set(SERVICE_REGISTRY))
        self.assertEqual(SERVICE_PAGES['s3']['title'], SERVICE_REGISTRY['s3'].title)

    @patch('dashboard.views.cloudfront_inventory')
    def test_cloudfront_api_returns_inventory(self, cloudfront_inventory):
        cloudfront_inventory.return_value = {'summary': {'distributions': 1}, 'distributions': []}

        response = self.client.get(reverse('dashboard:cloudfront'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['distributions'], 1)

    @patch('dashboard.views.config_inventory')
    def test_config_api_returns_inventory(self, config_inventory):
        config_inventory.return_value = {'summary': {'config_rules': 1}, 'config_rules': []}

        response = self.client.get(reverse('dashboard:config'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['config_rules'], 1)

    @patch('dashboard.views.list_resources')
    def test_resources_api_passes_selected_services(self, list_resources):
        list_resources.return_value = []

        response = self.client.get(
            reverse('dashboard:resources'),
            {'services': 's3,lambda,not-a-service,logs'},
        )

        self.assertEqual(response.status_code, 200)
        list_resources.assert_called_once_with({'s3', 'lambda', 'cloudwatch'})

    @patch('dashboard.views.list_resources')
    def test_resources_api_without_selection_loads_all_services(self, list_resources):
        list_resources.return_value = []

        response = self.client.get(reverse('dashboard:resources'))

        self.assertEqual(response.status_code, 200)
        list_resources.assert_called_once_with(None)

    @patch('dashboard.views.list_resources')
    def test_resources_api_returns_json_for_parser_errors(self, list_resources):
        list_resources.side_effect = ResponseParserError('invalid XML received')

        response = self.client.get(reverse('dashboard:resources'))

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertEqual(response.json()['resources'], [])
        self.assertIn('invalid XML received', response.json()['error'])


class ResourceLoaderFilterTests(SimpleTestCase):
    @patch('dashboard.aws._resource')
    def test_list_resources_filters_loaders_by_selected_services(self, resource):
        resource.side_effect = lambda name, label, loader: ResourceResult(
            name=name,
            label=label,
            count=0,
            items=[],
        )

        results = aws_list_resources({'s3', 'lambda'})

        self.assertEqual([result.name for result in results], ['s3-buckets', 'lambda-functions'])
        self.assertEqual(resource.call_count, 2)


class FlociClientFactoryTests(SimpleTestCase):
    def test_credential_context_reports_environment_credentials(self):
        with patch.dict(os.environ, {
            'AWS_ACCESS_KEY_ID': 'test',
            'AWS_SECRET_ACCESS_KEY': 'test',
        }, clear=True):
            factory = FlociClientFactory()

            self.assertEqual(factory.credential_context(), {
                'credential_source': 'environment',
                'endpoint_source': 'settings',
                'has_env_credentials': True,
                'identity_expires_at': None,
                'identity_label': None,
                'identity_type': None,
                'profile': None,
                'profile_source': None,
                'region_source': 'settings',
            })

    def test_credential_context_reports_configured_profile(self):
        with patch.dict(os.environ, {'AWS_PROFILE': 'developer'}, clear=True):
            factory = FlociClientFactory()

            self.assertEqual(factory.credential_context(), {
                'credential_source': 'profile',
                'endpoint_source': 'settings',
                'has_env_credentials': False,
                'identity_expires_at': None,
                'identity_label': None,
                'identity_type': None,
                'profile': 'developer',
                'profile_source': 'AWS_PROFILE',
                'region_source': 'settings',
            })

    def test_aws_endpoint_url_is_accepted_for_local_bootstrap(self):
        with patch.dict(os.environ, {
            'AWS_ENDPOINT_URL': 'http://localhost:4566',
            'AWS_DEFAULT_REGION': 'us-east-1',
            'AWS_ACCESS_KEY_ID': 'test',
            'AWS_SECRET_ACCESS_KEY': 'test',
        }, clear=True):
            factory = FlociClientFactory()

            self.assertEqual(factory.endpoint_url, 'http://localhost:4566')
            self.assertEqual(factory.endpoint_source, 'AWS_ENDPOINT_URL')
            self.assertEqual(factory.profile, None)
            self.assertEqual(factory.credential_context()['credential_source'], 'environment')

    def test_floci_endpoint_env_var_is_accepted_for_sidecar_bootstrap(self):
        with patch.dict(os.environ, {
            'FLOCI_ENDPOINT': 'http://host.docker.internal:4566',
            'AWS_DEFAULT_REGION': 'us-east-1',
            'AWS_ACCESS_KEY_ID': 'test',
            'AWS_SECRET_ACCESS_KEY': 'test',
        }, clear=True):
            factory = FlociClientFactory()

            self.assertEqual(factory.endpoint_url, 'http://host.docker.internal:4566')
            self.assertEqual(factory.endpoint_source, 'FLOCI_ENDPOINT')
            self.assertEqual(factory.profile, None)
            self.assertEqual(factory.credential_context()['credential_source'], 'environment')

    def test_local_test_credentials_are_default_when_profile_is_unavailable(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            'dashboard.aws.boto3.Session',
            side_effect=ProfileNotFound(profile='floci-admin'),
        ):
            factory = FlociClientFactory()

            self.assertEqual(factory.profile, None)
            self.assertEqual(factory.access_key_id, 'test')
            self.assertEqual(factory.secret_access_key, 'test')
            self.assertEqual(factory.credential_context()['credential_source'], 'local_default')
            self.assertEqual(factory.local_identity_hint()['user_id'], 'test')

    @override_settings(FLOCI_AWS_PROFILE='floci-admin')
    def test_settings_profile_is_used_when_available(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch('dashboard.aws.boto3.Session') as session:
                session.return_value.get_credentials.return_value = object()
                factory = FlociClientFactory()

        self.assertEqual(factory.profile, 'floci-admin')
        self.assertIsNone(factory.access_key_id)
        self.assertEqual(factory.credential_context()['credential_source'], 'profile')
        self.assertEqual(factory.credential_context()['profile_source'], 'settings')

    def test_localhost_floci_dns_endpoints_are_allowed(self):
        endpoints = [
            'http://floci:4566',
            'http://localhost.floci.io:4566',
            'http://s3.localhost.floci.io:4566',
            'http://bucket.s3.localhost.floci.io:4566',
            'http://localhost.localstack.cloud:4566',
            'http://s3.localhost.localstack.cloud:4566',
        ]

        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {'FLOCI_AWS_ENDPOINT_URL': endpoint}):
                factory = FlociClientFactory()

                self.assertEqual(factory.endpoint_url, endpoint)

    def test_non_local_endpoints_are_rejected(self):
        with patch.dict(os.environ, {'FLOCI_AWS_ENDPOINT_URL': 'https://aws.amazon.com'}):
            with self.assertRaisesMessage(ValueError, 'Refusing to use a non-local AWS endpoint'):
                FlociClientFactory()


class StepFunctionsActionTests(SimpleTestCase):
    @patch('dashboard.stepfunctions_views.publish_state_machine_version')
    def test_publish_state_machine_version_endpoint_uses_action_helper(self, publish_version):
        publish_version.return_value = {
            'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:orders',
            'state_machine_version_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:orders:1',
        }

        response = self.client.post(
            reverse('dashboard:stepfunctions-state-machine-versions-publish'),
            data=json.dumps({
                'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:orders',
                'revision_id': 'rev-1',
                'description': 'stable test version',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()['state_machine_version_arn'],
            'arn:aws:states:us-east-1:000000000000:stateMachine:orders:1',
        )
        publish_version.assert_called_once_with(
            'arn:aws:states:us-east-1:000000000000:stateMachine:orders',
            revision_id='rev-1',
            description='stable test version',
        )

    @patch('dashboard.stepfunctions_views.delete_state_machine_version')
    def test_delete_state_machine_version_endpoint_uses_action_helper(self, delete_version):
        version_arn = 'arn:aws:states:us-east-1:000000000000:stateMachine:orders:1'
        delete_version.return_value = {'state_machine_version_arn': version_arn}

        response = self.client.delete(
            reverse('dashboard:stepfunctions-state-machine-version-delete'),
            data=json.dumps({'state_machine_version_arn': version_arn}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['state_machine_version_arn'], version_arn)
        delete_version.assert_called_once_with(version_arn)

    @patch('dashboard.stepfunctions_views.start_execution')
    def test_start_execution_endpoint_uses_action_helper(self, start_execution):
        start_execution.return_value = {
            'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:orders',
            'execution_arn': 'arn:aws:states:us-east-1:000000000000:execution:orders:test',
        }

        response = self.client.post(
            reverse('dashboard:stepfunctions-executions-start'),
            data=json.dumps({
                'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:orders',
                'name': 'test',
                'input': {'order_id': '123'},
                'trace_header': 'trace-1',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['execution_arn'], 'arn:aws:states:us-east-1:000000000000:execution:orders:test')
        start_execution.assert_called_once_with(
            'arn:aws:states:us-east-1:000000000000:stateMachine:orders',
            {'order_id': '123'},
            name='test',
            trace_header='trace-1',
        )

    @patch('dashboard.stepfunctions_views.stop_execution')
    def test_stop_execution_endpoint_uses_action_helper(self, stop_execution):
        stop_execution.return_value = {
            'execution_arn': 'arn:aws:states:us-east-1:000000000000:execution:orders:test',
        }

        response = self.client.post(
            reverse('dashboard:stepfunctions-executions-stop'),
            data=json.dumps({
                'execution_arn': 'arn:aws:states:us-east-1:000000000000:execution:orders:test',
                'error': 'StoppedByDashboard',
                'cause': 'local test',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['execution_arn'], 'arn:aws:states:us-east-1:000000000000:execution:orders:test')
        stop_execution.assert_called_once_with(
            'arn:aws:states:us-east-1:000000000000:execution:orders:test',
            error='StoppedByDashboard',
            cause='local test',
        )


class EventBridgeActionTests(SimpleTestCase):
    @patch('dashboard.eventbridge_views.create_event_bus')
    def test_create_event_bus_endpoint(self, create_event_bus):
        create_event_bus.return_value = {'name': 'orders', 'arn': 'arn:bus'}
        response = self.client.post(reverse('dashboard:eventbridge-buses-create'), data=json.dumps({'name': 'orders', 'description': 'Order events'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        create_event_bus.assert_called_once_with('orders', description='Order events')

    @patch('dashboard.eventbridge_views.delete_event_bus')
    def test_delete_event_bus_endpoint(self, delete_event_bus):
        delete_event_bus.return_value = {'name': 'orders', 'deleted': True}
        response = self.client.post(reverse('dashboard:eventbridge-buses-delete'), data=json.dumps({'name': 'orders'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        delete_event_bus.assert_called_once_with('orders')

    @patch('dashboard.eventbridge_views.put_rule')
    def test_put_rule_endpoint(self, put_rule):
        put_rule.return_value = {'name': 'created', 'state': 'ENABLED'}
        response = self.client.post(reverse('dashboard:eventbridge-rules-put'), data=json.dumps({'name': 'created', 'event_bus_name': 'orders', 'event_pattern': {'source': ['app']}}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        put_rule.assert_called_once_with('created', 'orders', event_pattern={'source': ['app']}, schedule_expression='', description='', state='ENABLED')

    @patch('dashboard.eventbridge_views.set_rule_state')
    def test_set_rule_state_endpoint(self, set_rule_state):
        set_rule_state.return_value = {'name': 'created', 'state': 'DISABLED'}
        response = self.client.post(reverse('dashboard:eventbridge-rules-state'), data=json.dumps({'name': 'created', 'event_bus_name': 'orders', 'enabled': False}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        set_rule_state.assert_called_once_with('created', 'orders', enabled=False)

    @patch('dashboard.eventbridge_views.delete_rule')
    def test_delete_rule_endpoint(self, delete_rule):
        delete_rule.return_value = {'name': 'created', 'deleted': True}
        response = self.client.post(reverse('dashboard:eventbridge-rules-delete'), data=json.dumps({'name': 'created', 'event_bus_name': 'orders'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        delete_rule.assert_called_once_with('created', 'orders')

    @patch('dashboard.eventbridge_views.put_target')
    def test_put_target_endpoint(self, put_target):
        put_target.return_value = {'target': {'Id': 'queue', 'Arn': 'arn:sqs'}, 'failed_entry_count': 0}
        response = self.client.post(reverse('dashboard:eventbridge-targets-put'), data=json.dumps({'rule_name': 'created', 'event_bus_name': 'orders', 'target_id': 'queue', 'arn': 'arn:sqs', 'input': {'kind': 'order'}}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        put_target.assert_called_once_with('created', 'orders', 'queue', 'arn:sqs', role_arn='', input_value={'kind': 'order'})

    @patch('dashboard.eventbridge_views.remove_target')
    def test_remove_target_endpoint(self, remove_target):
        remove_target.return_value = {'target_id': 'queue', 'removed': True}
        response = self.client.post(reverse('dashboard:eventbridge-targets-remove'), data=json.dumps({'rule_name': 'created', 'event_bus_name': 'orders', 'target_id': 'queue'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        remove_target.assert_called_once_with('created', 'orders', 'queue')

    @patch('dashboard.eventbridge_views.put_event')
    def test_put_event_endpoint_uses_action_helper(self, put_event):
        put_event.return_value = {
            'event_bus_name': 'default',
            'failed_entry_count': 0,
            'event_id': 'event-123',
            'entries': [{'EventId': 'event-123'}],
        }

        response = self.client.post(
            reverse('dashboard:eventbridge-events-put'),
            data=json.dumps({
                'event_bus_name': 'default',
                'source': 'com.example.orders',
                'detail_type': 'OrderCreated',
                'detail': {'order_id': '123'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['event_id'], 'event-123')
        put_event.assert_called_once_with(
            'default',
            'com.example.orders',
            'OrderCreated',
            {'order_id': '123'},
            resources=None,
        )

    def test_put_event_endpoint_rejects_invalid_json_detail(self):
        response = self.client.post(
            reverse('dashboard:eventbridge-events-put'),
            data=json.dumps({
                'event_bus_name': 'default',
                'source': 'com.example.orders',
                'detail_type': 'OrderCreated',
                'detail': '{bad json',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'eventbridge')
        self.assertEqual(response.json()['operation'], 'put_event')


class CloudFormationActionTests(SimpleTestCase):
    @patch('dashboard.cloudformation_views.validate_template')
    def test_validate_template_endpoint_uses_action_helper(self, validate_template):
        validate_template.return_value = {'validation': {'Description': 'ok'}}

        response = self.client.post(
            reverse('dashboard:cloudformation-templates-validate'),
            data=json.dumps({'template_body': 'Resources: {}'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['validation']['Description'], 'ok')
        validate_template.assert_called_once_with('Resources: {}')

    @patch('dashboard.cloudformation_views.create_stack')
    def test_create_stack_endpoint_uses_action_helper(self, create_stack):
        create_stack.return_value = {'stack_name': 'demo', 'stack_id': 'stack-123'}

        response = self.client.post(
            reverse('dashboard:cloudformation-stacks'),
            data=json.dumps({
                'stack_name': 'demo',
                'template_body': 'Resources: {}',
                'parameters': {'Env': 'dev'},
                'capabilities': 'CAPABILITY_NAMED_IAM',
                'disable_rollback': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['stack_id'], 'stack-123')
        create_stack.assert_called_once_with(
            'demo',
            'Resources: {}',
            parameters={'Env': 'dev'},
            capabilities='CAPABILITY_NAMED_IAM',
            disable_rollback=True,
        )

    @patch('dashboard.cloudformation_views.update_stack')
    def test_update_stack_endpoint_uses_action_helper(self, update_stack):
        update_stack.return_value = {'stack_name': 'demo', 'stack_id': 'stack-123'}

        response = self.client.put(
            reverse('dashboard:cloudformation-stack-detail', kwargs={'stack_name': 'demo'}),
            data=json.dumps({
                'template_body': 'Resources: {}',
                'parameters': [{'ParameterKey': 'Env', 'ParameterValue': 'dev'}],
                'capabilities': ['CAPABILITY_IAM'],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_stack.assert_called_once_with(
            'demo',
            'Resources: {}',
            parameters=[{'ParameterKey': 'Env', 'ParameterValue': 'dev'}],
            capabilities=['CAPABILITY_IAM'],
        )

    @patch('dashboard.cloudformation_views.delete_stack')
    def test_delete_stack_endpoint_uses_action_helper(self, delete_stack):
        delete_stack.return_value = {'stack_name': 'demo'}

        response = self.client.delete(
            reverse('dashboard:cloudformation-stack-detail', kwargs={'stack_name': 'demo'}),
        )

        self.assertEqual(response.status_code, 200)
        delete_stack.assert_called_once_with('demo')

    @patch('dashboard.cloudformation_views.create_change_set')
    def test_create_change_set_endpoint_uses_action_helper(self, create_change_set):
        create_change_set.return_value = {'stack_name': 'demo', 'change_set_name': 'next'}

        response = self.client.post(
            reverse('dashboard:cloudformation-change-sets'),
            data=json.dumps({
                'stack_name': 'demo',
                'change_set_name': 'next',
                'change_set_type': 'UPDATE',
                'template_body': 'Resources: {}',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_change_set.assert_called_once_with(
            'demo',
            'next',
            'Resources: {}',
            change_set_type='UPDATE',
            parameters=None,
            capabilities=None,
        )

    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_notes_include_1_5_25_provisioning_coverage(self, factory_mock, paginate_mock):
        cloudformation = MagicMock()
        factory_mock.return_value.client.return_value = cloudformation
        paginate_mock.return_value = []

        result = cloudformation_inventory()

        self.assertTrue(any(
            'Lambda-backed custom resources' in note and 'EC2 VPC/subnet resources' in note
            for note in result['notes']
        ))
        self.assertIn('Lambda layer versions', ' '.join(result['notes']))

    @patch('dashboard.cloudformation_views.describe_change_set')
    def test_describe_change_set_endpoint_uses_action_helper(self, describe_change_set):
        describe_change_set.return_value = {'change_set': {'ChangeSetName': 'next'}}

        response = self.client.get(
            reverse('dashboard:cloudformation-change-set-detail', kwargs={
                'stack_name': 'demo',
                'change_set_name': 'next',
            }),
        )

        self.assertEqual(response.status_code, 200)
        describe_change_set.assert_called_once_with('demo', 'next')

    @patch('dashboard.cloudformation_views.execute_change_set')
    def test_execute_change_set_endpoint_uses_action_helper(self, execute_change_set):
        execute_change_set.return_value = {'stack_name': 'demo', 'change_set_name': 'next'}

        response = self.client.post(
            reverse('dashboard:cloudformation-change-set-detail', kwargs={
                'stack_name': 'demo',
                'change_set_name': 'next',
            }),
        )

        self.assertEqual(response.status_code, 200)
        execute_change_set.assert_called_once_with('demo', 'next')

    @patch('dashboard.cloudformation_views.delete_change_set')
    def test_delete_change_set_endpoint_uses_action_helper(self, delete_change_set):
        delete_change_set.return_value = {'stack_name': 'demo', 'change_set_name': 'next'}

        response = self.client.delete(
            reverse('dashboard:cloudformation-change-set-detail', kwargs={
                'stack_name': 'demo',
                'change_set_name': 'next',
            }),
        )

        self.assertEqual(response.status_code, 200)
        delete_change_set.assert_called_once_with('demo', 'next')


class CognitoActionTests(SimpleTestCase):
    @patch('dashboard.cognito_views.create_user_pool')
    def test_create_user_pool_endpoint_uses_action_helper(self, create_user_pool):
        create_user_pool.return_value = {'pool_id': 'local-pool', 'name': 'MyApp'}

        response = self.client.post(
            reverse('dashboard:cognito-user-pools'),
            data=json.dumps({'name': 'MyApp', 'tags': {'floci:override-id': 'local-pool'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['pool_id'], 'local-pool')
        create_user_pool.assert_called_once_with('MyApp', tags={'floci:override-id': 'local-pool'})

    @patch('dashboard.cognito_views.delete_user_pool')
    def test_delete_user_pool_endpoint_uses_action_helper(self, delete_user_pool):
        delete_user_pool.return_value = {'user_pool_id': 'local-pool'}

        response = self.client.delete(
            reverse('dashboard:cognito-user-pool-detail', kwargs={'user_pool_id': 'local-pool'}),
        )

        self.assertEqual(response.status_code, 200)
        delete_user_pool.assert_called_once_with('local-pool')

    @patch('dashboard.cognito_views.create_user_pool_client')
    def test_create_user_pool_client_endpoint_uses_action_helper(self, create_client):
        create_client.return_value = {'client_id': 'client-123', 'client_secret': 'secret'}

        response = self.client.post(
            reverse('dashboard:cognito-user-pool-clients', kwargs={'user_pool_id': 'local-pool'}),
            data=json.dumps({
                'name': 'my-client',
                'generate_secret': True,
                'allowed_oauth_scopes': ['notes/read'],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['client_id'], 'client-123')
        create_client.assert_called_once_with(
            'local-pool',
            'my-client',
            generate_secret=True,
            allowed_oauth_scopes=['notes/read'],
        )

    @patch('dashboard.cognito_views.create_resource_server')
    def test_create_resource_server_endpoint_uses_action_helper(self, create_resource_server):
        create_resource_server.return_value = {'resource_server': {'Identifier': 'notes'}}

        response = self.client.post(
            reverse('dashboard:cognito-resource-servers', kwargs={'user_pool_id': 'local-pool'}),
            data=json.dumps({
                'identifier': 'notes',
                'name': 'Notes API',
                'scopes': {'read': 'Read notes'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_resource_server.assert_called_once_with(
            'local-pool',
            'notes',
            'Notes API',
            scopes={'read': 'Read notes'},
        )

    @patch('dashboard.cognito_views.admin_create_user')
    def test_admin_create_user_endpoint_uses_action_helper(self, admin_create_user):
        admin_create_user.return_value = {'user': {'Username': 'alice@example.com'}}

        response = self.client.post(
            reverse('dashboard:cognito-users', kwargs={'user_pool_id': 'local-pool'}),
            data=json.dumps({
                'username': 'alice@example.com',
                'temporary_password': 'Temp1234!',
                'attributes': {'email': 'alice@example.com'},
                'message_action': 'SUPPRESS',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        admin_create_user.assert_called_once_with(
            'local-pool',
            'alice@example.com',
            temporary_password='Temp1234!',
            attributes={'email': 'alice@example.com'},
            message_action='SUPPRESS',
        )

    @patch('dashboard.cognito_views.admin_set_user_password')
    def test_set_user_password_endpoint_uses_action_helper(self, set_password):
        set_password.return_value = {'username': 'alice@example.com', 'permanent': True}

        response = self.client.post(
            reverse('dashboard:cognito-user-password', kwargs={
                'user_pool_id': 'local-pool',
                'username': 'alice@example.com',
            }),
            data=json.dumps({'password': 'Perm1234!', 'permanent': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        set_password.assert_called_once_with('local-pool', 'alice@example.com', 'Perm1234!', permanent=True)

    @patch('dashboard.cognito_views.admin_delete_user')
    def test_admin_delete_user_endpoint_uses_action_helper(self, admin_delete_user):
        admin_delete_user.return_value = {'username': 'alice@example.com'}

        response = self.client.delete(
            reverse('dashboard:cognito-user-detail', kwargs={
                'user_pool_id': 'local-pool',
                'username': 'alice@example.com',
            }),
        )

        self.assertEqual(response.status_code, 200)
        admin_delete_user.assert_called_once_with('local-pool', 'alice@example.com')

    @patch('dashboard.cognito_views.create_group')
    def test_create_group_endpoint_uses_action_helper(self, create_group):
        create_group.return_value = {'group': {'GroupName': 'admin'}}

        response = self.client.post(
            reverse('dashboard:cognito-groups', kwargs={'user_pool_id': 'local-pool'}),
            data=json.dumps({'group_name': 'admin', 'description': 'Admin group'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_group.assert_called_once_with('local-pool', 'admin', description='Admin group')

    @patch('dashboard.cognito_views.add_user_to_group')
    def test_add_user_to_group_endpoint_uses_action_helper(self, add_user_to_group):
        add_user_to_group.return_value = {'username': 'alice@example.com', 'group_name': 'admin'}

        response = self.client.post(
            reverse('dashboard:cognito-group-membership', kwargs={
                'user_pool_id': 'local-pool',
                'group_name': 'admin',
                'username': 'alice@example.com',
            }),
        )

        self.assertEqual(response.status_code, 200)
        add_user_to_group.assert_called_once_with('local-pool', 'alice@example.com', 'admin')

    @patch('dashboard.cognito_views.initiate_auth')
    def test_initiate_auth_endpoint_uses_action_helper(self, initiate_auth):
        initiate_auth.return_value = {'AuthenticationResult': {'AccessToken': 'token'}}

        response = self.client.post(
            reverse('dashboard:cognito-auth-initiate'),
            data=json.dumps({
                'client_id': 'client-123',
                'username': 'alice@example.com',
                'password': 'Perm1234!',
                'auth_flow': 'USER_PASSWORD_AUTH',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        initiate_auth.assert_called_once_with(
            'client-123',
            'alice@example.com',
            'Perm1234!',
            auth_flow='USER_PASSWORD_AUTH',
        )

    @patch('dashboard.cognito_views.oauth_client_credentials')
    def test_oauth_token_endpoint_uses_action_helper(self, oauth_client_credentials):
        oauth_client_credentials.return_value = {'access_token': 'token', 'token_type': 'Bearer'}

        response = self.client.post(
            reverse('dashboard:cognito-oauth-token'),
            data=json.dumps({
                'client_id': 'client-123',
                'client_secret': 'secret',
                'scope': 'notes/read',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        oauth_client_credentials.assert_called_once_with('client-123', 'secret', scope='notes/read')


class RDSActionTests(SimpleTestCase):
    @patch('dashboard.rds_views.create_db_instance')
    def test_create_db_instance_endpoint_uses_action_helper(self, create_db_instance):
        create_db_instance.return_value = {'identifier': 'mypostgres'}

        response = self.client.post(
            reverse('dashboard:rds-instances'),
            data=json.dumps({
                'identifier': 'mypostgres',
                'engine': 'postgres',
                'username': 'admin',
                'password': 'secret123',
                'db_instance_class': 'db.t3.micro',
                'allocated_storage': 20,
                'db_name': 'appdb',
                'engine_version': 'postgres:16-alpine',
                'enable_iam_auth': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['identifier'], 'mypostgres')
        create_db_instance.assert_called_once_with(
            'mypostgres',
            'postgres',
            'admin',
            'secret123',
            db_instance_class='db.t3.micro',
            allocated_storage=20,
            db_name='appdb',
            engine_version='postgres:16-alpine',
            enable_iam_auth=True,
            tags=None,
        )

    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_notes_include_provisioning_and_sts_iam_auth_context(self, factory_mock, paginate_mock):
        rds = MagicMock()
        factory_mock.return_value.client.return_value = rds
        paginate_mock.return_value = []

        result = rds_inventory()

        self.assertTrue(any('RDS provisioning lifecycle support' in note for note in result['notes']))
        self.assertTrue(any('STS session secret keys' in note for note in result['notes']))
        self.assertIn('CreateDBInstance', result['supported'])
        self.assertIn('ModifyDBInstance', result['supported'])
        self.assertIn('DeleteDBInstance', result['supported'])

    @patch('dashboard.rds_views.modify_db_instance')
    def test_modify_db_instance_endpoint_uses_action_helper(self, modify_db_instance):
        modify_db_instance.return_value = {'identifier': 'mypostgres'}

        response = self.client.put(
            reverse('dashboard:rds-instance-detail', kwargs={'identifier': 'mypostgres'}),
            data=json.dumps({
                'db_instance_class': 'db.t3.small',
                'allocated_storage': 30,
                'master_user_password': 'newsecret123',
                'apply_immediately': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        modify_db_instance.assert_called_once_with(
            'mypostgres',
            db_instance_class='db.t3.small',
            allocated_storage=30,
            master_user_password='newsecret123',
            apply_immediately=True,
        )

    @patch('dashboard.rds_views.reboot_db_instance')
    def test_reboot_db_instance_endpoint_uses_action_helper(self, reboot_db_instance):
        reboot_db_instance.return_value = {'identifier': 'mypostgres'}

        response = self.client.post(
            reverse('dashboard:rds-instance-reboot', kwargs={'identifier': 'mypostgres'}),
        )

        self.assertEqual(response.status_code, 200)
        reboot_db_instance.assert_called_once_with('mypostgres')

    @patch('dashboard.rds_views.delete_db_instance')
    def test_delete_db_instance_endpoint_uses_action_helper(self, delete_db_instance):
        delete_db_instance.return_value = {'identifier': 'mypostgres'}

        response = self.client.delete(
            reverse('dashboard:rds-instance-detail', kwargs={'identifier': 'mypostgres'}),
            data=json.dumps({
                'skip_final_snapshot': False,
                'final_snapshot_identifier': 'mypostgres-final',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        delete_db_instance.assert_called_once_with(
            'mypostgres',
            skip_final_snapshot=False,
            final_snapshot_identifier='mypostgres-final',
        )

    @patch('dashboard.rds_views.create_db_cluster')
    def test_create_db_cluster_endpoint_uses_action_helper(self, create_db_cluster):
        create_db_cluster.return_value = {'identifier': 'mycluster'}

        response = self.client.post(
            reverse('dashboard:rds-clusters'),
            data=json.dumps({
                'identifier': 'mycluster',
                'engine': 'postgres',
                'username': 'admin',
                'password': 'secret123',
                'database_name': 'appdb',
                'engine_version': 'postgres:16-alpine',
                'enable_iam_auth': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_db_cluster.assert_called_once_with(
            'mycluster',
            'postgres',
            'admin',
            'secret123',
            database_name='appdb',
            engine_version='postgres:16-alpine',
            enable_iam_auth=True,
        )

    @patch('dashboard.rds_views.delete_db_cluster')
    def test_delete_db_cluster_endpoint_uses_action_helper(self, delete_db_cluster):
        delete_db_cluster.return_value = {'identifier': 'mycluster'}

        response = self.client.delete(
            reverse('dashboard:rds-cluster-detail', kwargs={'identifier': 'mycluster'}),
        )

        self.assertEqual(response.status_code, 200)
        delete_db_cluster.assert_called_once_with(
            'mycluster',
            skip_final_snapshot=True,
            final_snapshot_identifier='',
        )

    @patch('dashboard.rds_views.create_db_parameter_group')
    def test_create_db_parameter_group_endpoint_uses_action_helper(self, create_db_parameter_group):
        create_db_parameter_group.return_value = {'parameter_group': {'DBParameterGroupName': 'local-postgres'}}

        response = self.client.post(
            reverse('dashboard:rds-parameter-groups'),
            data=json.dumps({
                'name': 'local-postgres',
                'family': 'postgres16',
                'description': 'Local PostgreSQL parameters',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_db_parameter_group.assert_called_once_with(
            'local-postgres',
            'postgres16',
            'Local PostgreSQL parameters',
        )

    @patch('dashboard.rds_views.delete_db_parameter_group')
    def test_delete_db_parameter_group_endpoint_uses_action_helper(self, delete_db_parameter_group):
        delete_db_parameter_group.return_value = {'name': 'local-postgres'}

        response = self.client.delete(
            reverse('dashboard:rds-parameter-group-detail', kwargs={'name': 'local-postgres'}),
        )

        self.assertEqual(response.status_code, 200)
        delete_db_parameter_group.assert_called_once_with('local-postgres')


class AutoScalingActionTests(SimpleTestCase):
    @patch('dashboard.autoscaling_views.create_launch_configuration')
    def test_create_launch_configuration_endpoint_uses_action_helper(self, create_launch_configuration):
        create_launch_configuration.return_value = {'name': 'my-lc'}

        response = self.client.post(
            reverse('dashboard:autoscaling-launch-configurations'),
            data=json.dumps({
                'name': 'my-lc',
                'image_id': 'ami-12345678',
                'instance_type': 't3.micro',
                'key_name': 'floci-key',
                'security_groups': ['sg-default'],
                'user_data': '#!/bin/sh\necho hello',
                'iam_instance_profile': 'app-profile',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_launch_configuration.assert_called_once_with(
            'my-lc',
            'ami-12345678',
            't3.micro',
            key_name='floci-key',
            security_groups=['sg-default'],
            user_data='#!/bin/sh\necho hello',
            iam_instance_profile='app-profile',
        )

    @patch('dashboard.autoscaling_views.delete_launch_configuration')
    def test_delete_launch_configuration_endpoint_uses_action_helper(self, delete_launch_configuration):
        delete_launch_configuration.return_value = {'name': 'my-lc'}

        response = self.client.delete(
            reverse('dashboard:autoscaling-launch-configuration-detail', kwargs={'name': 'my-lc'}),
        )

        self.assertEqual(response.status_code, 200)
        delete_launch_configuration.assert_called_once_with('my-lc')

    @patch('dashboard.autoscaling_views.create_auto_scaling_group')
    def test_create_auto_scaling_group_endpoint_uses_action_helper(self, create_auto_scaling_group):
        create_auto_scaling_group.return_value = {'name': 'my-asg'}

        response = self.client.post(
            reverse('dashboard:autoscaling-groups'),
            data=json.dumps({
                'name': 'my-asg',
                'launch_configuration_name': 'my-lc',
                'min_size': 1,
                'max_size': 5,
                'desired_capacity': 2,
                'availability_zones': ['us-east-1a'],
                'target_group_arns': ['arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/my-tg/abc'],
                'tags': {'env': 'local'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_auto_scaling_group.assert_called_once_with(
            'my-asg',
            'my-lc',
            min_size=1,
            max_size=5,
            desired_capacity=2,
            availability_zones=['us-east-1a'],
            target_group_arns=['arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/my-tg/abc'],
            tags={'env': 'local'},
        )

    @patch('dashboard.autoscaling_views.update_auto_scaling_group')
    def test_update_auto_scaling_group_endpoint_uses_action_helper(self, update_auto_scaling_group):
        update_auto_scaling_group.return_value = {'name': 'my-asg'}

        response = self.client.put(
            reverse('dashboard:autoscaling-group-detail', kwargs={'name': 'my-asg'}),
            data=json.dumps({'min_size': 1, 'max_size': 6, 'desired_capacity': 3}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_auto_scaling_group.assert_called_once_with(
            'my-asg',
            launch_configuration_name='',
            min_size=1,
            max_size=6,
            desired_capacity=3,
            availability_zones=None,
        )

    @patch('dashboard.autoscaling_views.set_desired_capacity')
    def test_set_desired_capacity_endpoint_uses_action_helper(self, set_desired_capacity):
        set_desired_capacity.return_value = {'name': 'my-asg', 'desired_capacity': 3}

        response = self.client.post(
            reverse('dashboard:autoscaling-group-desired-capacity', kwargs={'name': 'my-asg'}),
            data=json.dumps({'desired_capacity': 3, 'honor_cooldown': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        set_desired_capacity.assert_called_once_with('my-asg', 3, honor_cooldown=True)

    @patch('dashboard.autoscaling_views.attach_instances')
    def test_attach_instances_endpoint_uses_action_helper(self, attach_instances):
        attach_instances.return_value = {'name': 'my-asg', 'instance_ids': ['i-123']}

        response = self.client.post(
            reverse('dashboard:autoscaling-group-instances', kwargs={'name': 'my-asg'}),
            data=json.dumps({'instance_ids': ['i-123']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        attach_instances.assert_called_once_with('my-asg', ['i-123'])

    @patch('dashboard.autoscaling_views.detach_instances')
    def test_detach_instances_endpoint_uses_action_helper(self, detach_instances):
        detach_instances.return_value = {'name': 'my-asg', 'instance_ids': ['i-123']}

        response = self.client.delete(
            reverse('dashboard:autoscaling-group-instances', kwargs={'name': 'my-asg'}),
            data=json.dumps({'instance_ids': ['i-123'], 'decrement_desired_capacity': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        detach_instances.assert_called_once_with('my-asg', ['i-123'], decrement_desired_capacity=True)

    @patch('dashboard.autoscaling_views.terminate_instance')
    def test_terminate_instance_endpoint_uses_action_helper(self, terminate_instance):
        terminate_instance.return_value = {'instance_id': 'i-123'}

        response = self.client.post(
            reverse('dashboard:autoscaling-instance-terminate', kwargs={'instance_id': 'i-123'}),
            data=json.dumps({'decrement_desired_capacity': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        terminate_instance.assert_called_once_with('i-123', decrement_desired_capacity=True)

    @patch('dashboard.autoscaling_views.put_lifecycle_hook')
    def test_put_lifecycle_hook_endpoint_uses_action_helper(self, put_lifecycle_hook):
        put_lifecycle_hook.return_value = {'name': 'my-asg', 'hook_name': 'launch-wait'}

        response = self.client.post(
            reverse('dashboard:autoscaling-lifecycle-hooks', kwargs={'name': 'my-asg'}),
            data=json.dumps({
                'hook_name': 'launch-wait',
                'transition': 'autoscaling:EC2_INSTANCE_LAUNCHING',
                'default_result': 'CONTINUE',
                'heartbeat_timeout': 120,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        put_lifecycle_hook.assert_called_once_with(
            'my-asg',
            'launch-wait',
            'autoscaling:EC2_INSTANCE_LAUNCHING',
            default_result='CONTINUE',
            heartbeat_timeout=120,
        )

    @patch('dashboard.autoscaling_views.put_scaling_policy')
    def test_put_scaling_policy_endpoint_uses_action_helper(self, put_scaling_policy):
        put_scaling_policy.return_value = {'name': 'my-asg', 'policy_name': 'scale-out-one'}

        response = self.client.post(
            reverse('dashboard:autoscaling-policies', kwargs={'name': 'my-asg'}),
            data=json.dumps({
                'policy_name': 'scale-out-one',
                'adjustment_type': 'ChangeInCapacity',
                'scaling_adjustment': 1,
                'cooldown': 60,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        put_scaling_policy.assert_called_once_with(
            'my-asg',
            'scale-out-one',
            adjustment_type='ChangeInCapacity',
            scaling_adjustment=1,
            cooldown=60,
        )


class ElasticLoadBalancingActionTests(SimpleTestCase):
    load_balancer_arn = 'arn:aws:elasticloadbalancing:us-east-1:000000000000:loadbalancer/app/my-alb/abc123'
    target_group_arn = 'arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/my-targets/def456'
    listener_arn = 'arn:aws:elasticloadbalancing:us-east-1:000000000000:listener/app/my-alb/abc123/ghi789'
    rule_arn = 'arn:aws:elasticloadbalancing:us-east-1:000000000000:listener-rule/app/my-alb/abc123/ghi789/jkl012'

    @patch('dashboard.elasticloadbalancing_views.create_load_balancer')
    def test_create_load_balancer_endpoint_uses_action_helper(self, create_load_balancer):
        create_load_balancer.return_value = {'load_balancers': [{'LoadBalancerName': 'my-alb'}]}

        response = self.client.post(
            reverse('dashboard:elbv2-load-balancers'),
            data=json.dumps({
                'name': 'my-alb',
                'type': 'application',
                'scheme': 'internet-facing',
                'subnets': ['subnet-a'],
                'security_groups': ['sg-default'],
                'ip_address_type': 'ipv4',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_load_balancer.assert_called_once_with(
            'my-alb',
            lb_type='application',
            scheme='internet-facing',
            subnets=['subnet-a'],
            security_groups=['sg-default'],
            ip_address_type='ipv4',
        )

    @patch('dashboard.elasticloadbalancing_views.delete_load_balancer')
    def test_delete_load_balancer_endpoint_uses_action_helper(self, delete_load_balancer):
        delete_load_balancer.return_value = {'load_balancer_arn': self.load_balancer_arn}

        response = self.client.delete(
            reverse('dashboard:elbv2-load-balancer-detail', kwargs={'load_balancer_arn': self.load_balancer_arn}),
        )

        self.assertEqual(response.status_code, 200)
        delete_load_balancer.assert_called_once_with(self.load_balancer_arn)

    @patch('dashboard.elasticloadbalancing_views.create_target_group')
    def test_create_target_group_endpoint_uses_action_helper(self, create_target_group):
        create_target_group.return_value = {'target_groups': [{'TargetGroupName': 'my-targets'}]}

        response = self.client.post(
            reverse('dashboard:elbv2-target-groups'),
            data=json.dumps({
                'name': 'my-targets',
                'protocol': 'HTTP',
                'port': 80,
                'target_type': 'instance',
                'vpc_id': 'vpc-default',
                'health_check_path': '/health',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_target_group.assert_called_once_with(
            'my-targets',
            'HTTP',
            80,
            target_type='instance',
            vpc_id='vpc-default',
            health_check_path='/health',
        )

    @patch('dashboard.elasticloadbalancing_views.delete_target_group')
    def test_delete_target_group_endpoint_uses_action_helper(self, delete_target_group):
        delete_target_group.return_value = {'target_group_arn': self.target_group_arn}

        response = self.client.delete(
            reverse('dashboard:elbv2-target-group-detail', kwargs={'target_group_arn': self.target_group_arn}),
        )

        self.assertEqual(response.status_code, 200)
        delete_target_group.assert_called_once_with(self.target_group_arn)

    @patch('dashboard.elasticloadbalancing_views.register_targets')
    def test_register_targets_endpoint_uses_action_helper(self, register_targets):
        register_targets.return_value = {'target_group_arn': self.target_group_arn}

        response = self.client.post(
            reverse('dashboard:elbv2-target-group-targets', kwargs={'target_group_arn': self.target_group_arn}),
            data=json.dumps({'targets': [{'Id': 'i-123', 'Port': 8080}]}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        register_targets.assert_called_once_with(self.target_group_arn, [{'Id': 'i-123', 'Port': 8080}])

    @patch('dashboard.elasticloadbalancing_views.deregister_targets')
    def test_deregister_targets_endpoint_uses_action_helper(self, deregister_targets):
        deregister_targets.return_value = {'target_group_arn': self.target_group_arn}

        response = self.client.delete(
            reverse('dashboard:elbv2-target-group-targets', kwargs={'target_group_arn': self.target_group_arn}),
            data=json.dumps({'targets': [{'Id': 'i-123'}]}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        deregister_targets.assert_called_once_with(self.target_group_arn, [{'Id': 'i-123'}])

    @patch('dashboard.elasticloadbalancing_views.create_listener')
    def test_create_listener_endpoint_uses_action_helper(self, create_listener):
        create_listener.return_value = {'listeners': [{'Port': 80}]}

        response = self.client.post(
            reverse('dashboard:elbv2-listeners'),
            data=json.dumps({
                'load_balancer_arn': self.load_balancer_arn,
                'protocol': 'HTTP',
                'port': 80,
                'target_group_arn': self.target_group_arn,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_listener.assert_called_once_with(self.load_balancer_arn, 'HTTP', 80, self.target_group_arn)

    @patch('dashboard.elasticloadbalancing_views.delete_listener')
    def test_delete_listener_endpoint_uses_action_helper(self, delete_listener):
        delete_listener.return_value = {'listener_arn': self.listener_arn}

        response = self.client.delete(
            reverse('dashboard:elbv2-listener-detail', kwargs={'listener_arn': self.listener_arn}),
        )

        self.assertEqual(response.status_code, 200)
        delete_listener.assert_called_once_with(self.listener_arn)

    @patch('dashboard.elasticloadbalancing_views.create_rule')
    def test_create_rule_endpoint_uses_action_helper(self, create_rule):
        create_rule.return_value = {'rules': [{'Priority': '10'}]}

        response = self.client.post(
            reverse('dashboard:elbv2-rules'),
            data=json.dumps({
                'listener_arn': self.listener_arn,
                'priority': 10,
                'path_pattern': '/api/*',
                'target_group_arn': self.target_group_arn,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_rule.assert_called_once_with(self.listener_arn, 10, '/api/*', self.target_group_arn)

    @patch('dashboard.elasticloadbalancing_views.delete_rule')
    def test_delete_rule_endpoint_uses_action_helper(self, delete_rule):
        delete_rule.return_value = {'rule_arn': self.rule_arn}

        response = self.client.delete(
            reverse('dashboard:elbv2-rule-detail', kwargs={'rule_arn': self.rule_arn}),
        )

        self.assertEqual(response.status_code, 200)
        delete_rule.assert_called_once_with(self.rule_arn)

    @patch('dashboard.elasticloadbalancing_views.add_tags')
    def test_add_tags_endpoint_uses_action_helper(self, add_tags):
        add_tags.return_value = {'resource_arns': [self.load_balancer_arn]}

        response = self.client.post(
            reverse('dashboard:elbv2-tags'),
            data=json.dumps({'resource_arns': [self.load_balancer_arn], 'tags': {'env': 'dev'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        add_tags.assert_called_once_with([self.load_balancer_arn], {'env': 'dev'})


class EC2ActionTests(SimpleTestCase):
    def _empty_ec2_client(self):
        ec2 = MagicMock()
        ec2.describe_instances.return_value = {'Reservations': []}
        ec2.describe_vpcs.return_value = {'Vpcs': []}
        ec2.describe_subnets.return_value = {'Subnets': []}
        ec2.describe_security_groups.return_value = {'SecurityGroups': []}
        ec2.describe_security_group_rules.return_value = {'SecurityGroupRules': []}
        ec2.describe_key_pairs.return_value = {'KeyPairs': []}
        ec2.describe_images.return_value = {'Images': []}
        ec2.describe_tags.return_value = {'Tags': []}
        ec2.describe_internet_gateways.return_value = {'InternetGateways': []}
        ec2.describe_route_tables.return_value = {'RouteTables': []}
        ec2.describe_addresses.return_value = {'Addresses': []}
        ec2.describe_availability_zones.return_value = {'AvailabilityZones': []}
        ec2.describe_regions.return_value = {'Regions': []}
        ec2.describe_account_attributes.return_value = {'AccountAttributes': []}
        ec2.describe_instance_types.return_value = {'InstanceTypes': []}
        return ec2

    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_includes_vpc_endpoints(self, factory_mock, paginate_mock):
        endpoint = {
            'VpcEndpointId': 'vpce-123',
            'VpcEndpointType': 'Interface',
            'ServiceName': 'com.amazonaws.us-east-1.s3',
            'State': 'available',
            'VpcId': 'vpc-default',
        }
        factory_mock.return_value.client.return_value = self._empty_ec2_client()
        paginate_mock.side_effect = lambda _client, operation_name, _result_key, **_kwargs: (
            [endpoint] if operation_name == 'describe_vpc_endpoints' else []
        )

        result = ec2_inventory()

        self.assertEqual(result['summary']['vpc_endpoints'], 1)
        self.assertEqual(result['vpc_endpoints'], [endpoint])

    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_resource_count_includes_vpc_endpoints(self, factory_mock, paginate_mock):
        endpoint = {'VpcEndpointId': 'vpce-123'}
        factory_mock.return_value.client.return_value = self._empty_ec2_client()
        paginate_mock.side_effect = lambda _client, operation_name, _result_key, **_kwargs: (
            [endpoint] if operation_name == 'describe_vpc_endpoints' else []
        )

        results = aws_list_resources({'ec2'})

        self.assertEqual(results[0].name, 'ec2-resources')
        self.assertEqual(results[0].count, 1)
        self.assertEqual(results[0].items, [{'type': 'vpc_endpoint', 'id': 'vpce-123'}])

    @patch('dashboard.ec2_views.run_instances')
    def test_run_instances_endpoint_uses_action_helper(self, run_instances):
        run_instances.return_value = {
            'reservation_id': 'r-123',
            'instance_id': 'i-123',
            'instances': [{'id': 'i-123'}],
        }

        response = self.client.post(
            reverse('dashboard:ec2-instances-run'),
            data=json.dumps({
                'image_id': 'ami-amazonlinux2023',
                'instance_type': 't2.micro',
                'subnet_id': 'subnet-default-c',
                'security_group_ids': ['sg-default'],
                'key_name': 'floci-key',
                'user_data': '#!/bin/sh\necho hello',
                'iam_instance_profile_arn': 'arn:aws:iam::000000000000:instance-profile/app',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['instance_id'], 'i-123')
        run_instances.assert_called_once_with(
            'ami-amazonlinux2023',
            't2.micro',
            subnet_id='subnet-default-c',
            security_group_ids=['sg-default'],
            key_name='floci-key',
            user_data='#!/bin/sh\necho hello',
            iam_instance_profile_arn='arn:aws:iam::000000000000:instance-profile/app',
            tags=None,
        )

    def test_run_instances_endpoint_rejects_missing_image_id(self):
        response = self.client.post(
            reverse('dashboard:ec2-instances-run'),
            data=json.dumps({'instance_type': 't2.micro'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'ec2')
        self.assertEqual(response.json()['operation'], 'run_instances')

    @patch('dashboard.ec2_views.stop_instance')
    def test_stop_instance_endpoint_uses_action_helper(self, stop_instance):
        stop_instance.return_value = {'instance_id': 'i-123', 'state_changes': []}

        response = self.client.post(reverse('dashboard:ec2-instance-stop', kwargs={'instance_id': 'i-123'}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['instance_id'], 'i-123')
        stop_instance.assert_called_once_with('i-123')

    @patch('dashboard.ec2_views.start_instance')
    def test_start_instance_endpoint_uses_action_helper(self, start_instance):
        start_instance.return_value = {'instance_id': 'i-123', 'state_changes': []}

        response = self.client.post(reverse('dashboard:ec2-instance-start', kwargs={'instance_id': 'i-123'}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['instance_id'], 'i-123')
        start_instance.assert_called_once_with('i-123')

    @patch('dashboard.ec2_views.reboot_instance')
    def test_reboot_instance_endpoint_uses_action_helper(self, reboot_instance):
        reboot_instance.return_value = {'instance_id': 'i-123', 'state_changes': []}

        response = self.client.post(reverse('dashboard:ec2-instance-reboot', kwargs={'instance_id': 'i-123'}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['instance_id'], 'i-123')
        reboot_instance.assert_called_once_with('i-123')

    @patch('dashboard.ec2_views.terminate_instance')
    def test_terminate_instance_endpoint_uses_action_helper(self, terminate_instance):
        terminate_instance.return_value = {'instance_id': 'i-123', 'state_changes': []}

        response = self.client.post(reverse('dashboard:ec2-instance-terminate', kwargs={'instance_id': 'i-123'}))

        self.assertEqual(response.status_code, 200)
        terminate_instance.assert_called_once_with('i-123')

    @patch('dashboard.ec2_views.import_key_pair')
    def test_import_key_pair_endpoint_uses_action_helper(self, import_key_pair):
        import_key_pair.return_value = {'key_name': 'floci-key', 'fingerprint': 'aa:bb'}

        response = self.client.post(
            reverse('dashboard:ec2-key-pairs-import'),
            data=json.dumps({
                'key_name': 'floci-key',
                'public_key_material': 'ssh-rsa AAAA user@host',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['key_name'], 'floci-key')
        import_key_pair.assert_called_once_with('floci-key', 'ssh-rsa AAAA user@host')

    @patch('dashboard.ec2_views.update_instance_tags')
    def test_update_instance_tags_endpoint_uses_action_helper(self, update_instance_tags):
        update_instance_tags.return_value = {
            'instance_id': 'i-123',
            'tags': [{'Key': 'Name', 'Value': 'web'}],
            'removed': [],
        }

        response = self.client.put(
            reverse('dashboard:ec2-instance-tags', kwargs={'instance_id': 'i-123'}),
            data=json.dumps({'tags': {'Name': 'web'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tags'][0]['Value'], 'web')
        update_instance_tags.assert_called_once_with('i-123', {'Name': 'web'})

    @patch('dashboard.ec2_views.run_instance_command')
    def test_run_instance_command_endpoint_uses_ssm_helper(self, run_instance_command):
        run_instance_command.return_value = {
            'instance_id': 'i-123',
            'command_id': 'cmd-123',
            'status': 'InProgress',
        }

        response = self.client.post(
            reverse('dashboard:ec2-instance-commands', kwargs={'instance_id': 'i-123'}),
            data=json.dumps({'command': 'uname -a', 'timeout_seconds': 60}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['command_id'], 'cmd-123')
        run_instance_command.assert_called_once_with('i-123', 'uname -a', timeout_seconds=60)

    @patch('dashboard.ec2_views.list_instance_commands')
    def test_list_instance_commands_endpoint_uses_ssm_helper(self, list_instance_commands):
        list_instance_commands.return_value = {'instance_id': 'i-123', 'commands': []}

        response = self.client.get(
            reverse('dashboard:ec2-instance-commands', kwargs={'instance_id': 'i-123'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['commands'], [])
        list_instance_commands.assert_called_once_with('i-123')

    @patch('dashboard.ec2_views.instance_command_detail')
    def test_instance_command_detail_endpoint_uses_ssm_helper(self, instance_command_detail):
        instance_command_detail.return_value = {
            'instance_id': 'i-123',
            'command_id': 'cmd-123',
            'status': 'Success',
            'stdout': 'Linux',
            'stderr': '',
        }

        response = self.client.get(reverse(
            'dashboard:ec2-instance-command-detail',
            kwargs={'instance_id': 'i-123', 'command_id': 'cmd-123'},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['stdout'], 'Linux')
        instance_command_detail.assert_called_once_with('i-123', 'cmd-123')

    @patch('dashboard.ec2_api.FlociClientFactory')
    def test_update_instance_tags_replaces_removed_keys(self, factory_mock):
        from .ec2_api import update_instance_tags

        ec2 = MagicMock()
        ec2.describe_instances.return_value = {
            'Reservations': [{'Instances': [{'InstanceId': 'i-123', 'Tags': [
                {'Key': 'Name', 'Value': 'old'},
                {'Key': 'RemoveMe', 'Value': 'yes'},
            ]}]}],
        }
        factory_mock.return_value.client.return_value = ec2

        result = update_instance_tags('i-123', {'Name': 'web', 'Environment': 'dev'})

        ec2.delete_tags.assert_called_once_with(
            Resources=['i-123'],
            Tags=[{'Key': 'RemoveMe', 'Value': 'yes'}],
        )
        ec2.create_tags.assert_called_once_with(
            Resources=['i-123'],
            Tags=[{'Key': 'Name', 'Value': 'web'}, {'Key': 'Environment', 'Value': 'dev'}],
        )
        self.assertEqual(result['removed'], ['RemoveMe'])

    @patch('dashboard.ec2_api.FlociClientFactory')
    def test_run_instance_command_uses_aws_run_shell_script(self, factory_mock):
        from .ec2_api import run_instance_command

        ssm = MagicMock()
        ssm.send_command.return_value = {
            'Command': {'CommandId': 'cmd-123', 'Status': 'InProgress'},
        }
        factory_mock.return_value.client.return_value = ssm

        result = run_instance_command('i-123', 'uname -a', timeout_seconds=60)

        self.assertEqual(result['command_id'], 'cmd-123')
        ssm.send_command.assert_called_once_with(
            InstanceIds=['i-123'],
            DocumentName='AWS-RunShellScript',
            Parameters={'commands': ['uname -a']},
            TimeoutSeconds=60,
            Comment='Floci Dashboard EC2 command runner',
        )

    def test_vpc_and_subnet_network_endpoints(self):
        with patch('dashboard.ec2_views.create_vpc', return_value={'vpc_id': 'vpc-123'}) as create_vpc:
            response = self.client.post(
                reverse('dashboard:ec2-vpcs-create'),
                data=json.dumps({'cidr_block': '10.0.0.0/16', 'name': 'app'}),
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 200)
            create_vpc.assert_called_once_with('10.0.0.0/16', name='app')
        with patch('dashboard.ec2_views.delete_vpc', return_value={'vpc_id': 'vpc-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-vpc-delete', kwargs={'vpc_id': 'vpc-123'})).status_code, 200)
        with patch('dashboard.ec2_views.create_subnet', return_value={'subnet_id': 'subnet-123'}) as create_subnet:
            response = self.client.post(
                reverse('dashboard:ec2-subnets-create'),
                data=json.dumps({'vpc_id': 'vpc-123', 'cidr_block': '10.0.1.0/24', 'availability_zone': 'us-east-1a', 'name': 'app-a'}),
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 200)
            create_subnet.assert_called_once_with('vpc-123', '10.0.1.0/24', availability_zone='us-east-1a', name='app-a')
        with patch('dashboard.ec2_views.delete_subnet', return_value={'subnet_id': 'subnet-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-subnet-delete', kwargs={'subnet_id': 'subnet-123'})).status_code, 200)

    def test_security_group_network_endpoints(self):
        with patch('dashboard.ec2_views.create_security_group', return_value={'group_id': 'sg-123'}) as create_group:
            response = self.client.post(
                reverse('dashboard:ec2-security-groups-create'),
                data=json.dumps({'name': 'web', 'description': 'web access', 'vpc_id': 'vpc-123'}),
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 200)
            create_group.assert_called_once_with('web', 'web access', 'vpc-123')
        with patch('dashboard.ec2_views.change_security_group_rule', return_value={'group_id': 'sg-123'}):
            route = reverse('dashboard:ec2-security-group-rules', kwargs={'group_id': 'sg-123'})
            payload = json.dumps({'direction': 'ingress', 'rule': {'protocol': 'tcp', 'from_port': 80, 'to_port': 80, 'cidr': '0.0.0.0/0'}})
            self.assertEqual(self.client.post(route, data=payload, content_type='application/json').status_code, 200)
            self.assertEqual(self.client.delete(route, data=payload, content_type='application/json').status_code, 200)
        with patch('dashboard.ec2_views.delete_security_group', return_value={'group_id': 'sg-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-security-group-delete', kwargs={'group_id': 'sg-123'})).status_code, 200)

    def test_internet_gateway_network_endpoints(self):
        with patch('dashboard.ec2_views.create_internet_gateway', return_value={'internet_gateway_id': 'igw-123'}):
            self.assertEqual(self.client.post(reverse('dashboard:ec2-internet-gateways-create')).status_code, 200)
        attachment_route = reverse('dashboard:ec2-internet-gateway-attachment', kwargs={'gateway_id': 'igw-123'})
        with patch('dashboard.ec2_views.set_internet_gateway_attachment', return_value={'internet_gateway_id': 'igw-123'}):
            payload = json.dumps({'vpc_id': 'vpc-123'})
            self.assertEqual(self.client.put(attachment_route, data=payload, content_type='application/json').status_code, 200)
            self.assertEqual(self.client.delete(attachment_route, data=payload, content_type='application/json').status_code, 200)
        with patch('dashboard.ec2_views.delete_internet_gateway', return_value={'internet_gateway_id': 'igw-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-internet-gateway-delete', kwargs={'gateway_id': 'igw-123'})).status_code, 200)

    def test_route_table_network_endpoints(self):
        with patch('dashboard.ec2_views.create_route_table', return_value={'route_table_id': 'rtb-123'}):
            response = self.client.post(reverse('dashboard:ec2-route-tables-create'), data=json.dumps({'vpc_id': 'vpc-123'}), content_type='application/json')
            self.assertEqual(response.status_code, 200)
        association_route = reverse('dashboard:ec2-route-table-associations', kwargs={'route_table_id': 'rtb-123'})
        with patch('dashboard.ec2_views.change_route_table_association', return_value={'route_table_id': 'rtb-123'}):
            self.assertEqual(self.client.post(association_route, data=json.dumps({'subnet_id': 'subnet-123'}), content_type='application/json').status_code, 200)
            self.assertEqual(self.client.delete(association_route, data=json.dumps({'association_id': 'rtbassoc-123'}), content_type='application/json').status_code, 200)
        routes_route = reverse('dashboard:ec2-routes', kwargs={'route_table_id': 'rtb-123'})
        with patch('dashboard.ec2_views.change_route', return_value={'route_table_id': 'rtb-123'}):
            payload = json.dumps({'destination_cidr': '0.0.0.0/0', 'gateway_id': 'igw-123'})
            self.assertEqual(self.client.post(routes_route, data=payload, content_type='application/json').status_code, 200)
            self.assertEqual(self.client.delete(routes_route, data=payload, content_type='application/json').status_code, 200)
        with patch('dashboard.ec2_views.delete_route_table', return_value={'route_table_id': 'rtb-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-route-table-delete', kwargs={'route_table_id': 'rtb-123'})).status_code, 200)

    def test_elastic_ip_network_endpoints(self):
        with patch('dashboard.ec2_views.allocate_elastic_ip', return_value={'allocation_id': 'eipalloc-123'}):
            self.assertEqual(self.client.post(reverse('dashboard:ec2-elastic-ips-allocate')).status_code, 200)
        association_route = reverse('dashboard:ec2-elastic-ip-association', kwargs={'allocation_id': 'eipalloc-123'})
        with patch('dashboard.ec2_views.set_elastic_ip_association', return_value={'allocation_id': 'eipalloc-123'}):
            self.assertEqual(self.client.put(association_route, data=json.dumps({'instance_id': 'i-123'}), content_type='application/json').status_code, 200)
            self.assertEqual(self.client.delete(association_route, data=json.dumps({'association_id': 'eipassoc-123'}), content_type='application/json').status_code, 200)
        with patch('dashboard.ec2_views.release_elastic_ip', return_value={'allocation_id': 'eipalloc-123', 'released': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-elastic-ip-release', kwargs={'allocation_id': 'eipalloc-123'})).status_code, 200)

    def test_nat_gateway_and_vpc_endpoint_network_endpoints(self):
        with patch('dashboard.ec2_views.create_nat_gateway', return_value={'nat_gateway_id': 'nat-123'}):
            response = self.client.post(reverse('dashboard:ec2-nat-gateways-create'), data=json.dumps({'subnet_id': 'subnet-123', 'allocation_id': 'eipalloc-123'}), content_type='application/json')
            self.assertEqual(response.status_code, 200)
        with patch('dashboard.ec2_views.delete_nat_gateway', return_value={'nat_gateway_id': 'nat-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-nat-gateway-delete', kwargs={'nat_gateway_id': 'nat-123'})).status_code, 200)
        with patch('dashboard.ec2_views.create_vpc_endpoint', return_value={'vpc_endpoint_id': 'vpce-123'}):
            response = self.client.post(reverse('dashboard:ec2-vpc-endpoints-create'), data=json.dumps({'vpc_id': 'vpc-123', 'service_name': 'com.amazonaws.us-east-1.s3', 'endpoint_type': 'Gateway', 'route_table_ids': ['rtb-123']}), content_type='application/json')
            self.assertEqual(response.status_code, 200)
        with patch('dashboard.ec2_views.delete_vpc_endpoint', return_value={'vpc_endpoint_id': 'vpce-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-vpc-endpoint-delete', kwargs={'endpoint_id': 'vpce-123'})).status_code, 200)

    def test_network_acl_advanced_endpoints(self):
        with patch('dashboard.ec2_views.create_network_acl', return_value={'network_acl_id': 'acl-123'}):
            self.assertEqual(self.client.post(reverse('dashboard:ec2-network-acls-create'), data=json.dumps({'vpc_id': 'vpc-123'}), content_type='application/json').status_code, 200)
        entries_route = reverse('dashboard:ec2-network-acl-entries', kwargs={'network_acl_id': 'acl-123'})
        with patch('dashboard.ec2_views.put_network_acl_entry', return_value={'network_acl_id': 'acl-123'}):
            self.assertEqual(self.client.put(entries_route, data=json.dumps({'entry': {'rule_number': 100}}), content_type='application/json').status_code, 200)
        with patch('dashboard.ec2_views.delete_network_acl_entry', return_value={'network_acl_id': 'acl-123', 'deleted': True}):
            self.assertEqual(self.client.delete(entries_route, data=json.dumps({'rule_number': 100, 'egress': False}), content_type='application/json').status_code, 200)
        with patch('dashboard.ec2_views.replace_network_acl_association', return_value={'network_acl_id': 'acl-123'}):
            self.assertEqual(self.client.put(reverse('dashboard:ec2-network-acl-associations', kwargs={'network_acl_id': 'acl-123'}), data=json.dumps({'association_id': 'aclassoc-123'}), content_type='application/json').status_code, 200)
        with patch('dashboard.ec2_views.delete_network_acl', return_value={'network_acl_id': 'acl-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-network-acl-delete', kwargs={'network_acl_id': 'acl-123'})).status_code, 200)

    def test_flow_log_advanced_endpoints(self):
        with patch('dashboard.ec2_views.create_flow_log', return_value={'flow_log_id': 'fl-123'}):
            response = self.client.post(reverse('dashboard:ec2-flow-logs-create'), data=json.dumps({'resource_id': 'vpc-default', 'resource_type': 'VPC', 'traffic_type': 'ALL', 'destination': 'arn:aws:s3:::logs'}), content_type='application/json')
            self.assertEqual(response.status_code, 200)
        with patch('dashboard.ec2_views.view_flow_log', return_value={'flow_log_id': 'fl-123', 'records': []}):
            self.assertEqual(self.client.get(reverse('dashboard:ec2-flow-log-view', kwargs={'flow_log_id': 'fl-123'})).status_code, 200)
        with patch('dashboard.ec2_views.delete_flow_log', return_value={'flow_log_id': 'fl-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-flow-log-delete', kwargs={'flow_log_id': 'fl-123'})).status_code, 200)

    def test_volume_and_image_advanced_endpoints(self):
        with patch('dashboard.ec2_views.create_volume', return_value={'volume_id': 'vol-123'}):
            response = self.client.post(reverse('dashboard:ec2-volumes-create'), data=json.dumps({'availability_zone': 'us-east-1a', 'size': 8, 'volume_type': 'gp3'}), content_type='application/json')
            self.assertEqual(response.status_code, 200)
        with patch('dashboard.ec2_views.delete_volume', return_value={'volume_id': 'vol-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-volume-delete', kwargs={'volume_id': 'vol-123'})).status_code, 200)
        with patch('dashboard.ec2_views.register_image', return_value={'image_id': 'ami-123'}):
            response = self.client.post(reverse('dashboard:ec2-images-register'), data=json.dumps({'name': 'app', 'description': 'App image', 'architecture': 'x86_64', 'root_device_name': '/dev/xvda'}), content_type='application/json')
            self.assertEqual(response.status_code, 200)

    def test_launch_template_advanced_endpoints(self):
        with patch('dashboard.ec2_views.create_launch_template', return_value={'launch_template_id': 'lt-123'}):
            self.assertEqual(self.client.post(reverse('dashboard:ec2-launch-templates-create'), data=json.dumps({'name': 'app', 'data': {'image_id': 'ami-123'}}), content_type='application/json').status_code, 200)
        with patch('dashboard.ec2_views.create_launch_template_version', return_value={'launch_template_id': 'lt-123', 'version_number': 2}):
            self.assertEqual(self.client.post(reverse('dashboard:ec2-launch-template-versions-create', kwargs={'launch_template_id': 'lt-123'}), data=json.dumps({'source_version': '1', 'data': {'instance_type': 't3.micro'}}), content_type='application/json').status_code, 200)
        with patch('dashboard.ec2_views.set_launch_template_default_version', return_value={'launch_template_id': 'lt-123', 'default_version': '2'}):
            self.assertEqual(self.client.put(reverse('dashboard:ec2-launch-template-default-version', kwargs={'launch_template_id': 'lt-123'}), data=json.dumps({'version': 2}), content_type='application/json').status_code, 200)
        with patch('dashboard.ec2_views.delete_launch_template', return_value={'launch_template_id': 'lt-123', 'deleted': True}):
            self.assertEqual(self.client.delete(reverse('dashboard:ec2-launch-template-delete', kwargs={'launch_template_id': 'lt-123'})).status_code, 200)

    def test_spot_request_advanced_endpoints(self):
        with patch('dashboard.ec2_views.request_spot_instances', return_value={'spot_request_ids': ['sir-123']}):
            response = self.client.post(reverse('dashboard:ec2-spot-requests-create'), data=json.dumps({'image_id': 'ami-123', 'instance_type': 't3.micro', 'instance_count': 1}), content_type='application/json')
            self.assertEqual(response.status_code, 200)
        with patch('dashboard.ec2_views.cancel_spot_request', return_value={'spot_request_id': 'sir-123', 'cancelled': []}):
            self.assertEqual(self.client.post(reverse('dashboard:ec2-spot-request-cancel', kwargs={'spot_request_id': 'sir-123'})).status_code, 200)


class IAMActionTests(SimpleTestCase):
    @patch('dashboard.iam_api.FlociClientFactory')
    def test_create_user_helper_adds_get_caller_identity_baseline_policy(self, factory_mock):
        from .iam_api import create_user

        iam = MagicMock()
        iam.create_user.return_value = {
            'User': {
                'UserName': 'charlie',
                'Arn': 'arn:aws:iam::000000000000:user/charlie',
                'UserId': 'AIDACHARLIE',
            },
        }
        factory_mock.return_value.client.return_value = iam

        result = create_user('charlie')

        self.assertEqual(result['user_name'], 'charlie')
        self.assertEqual(result['baseline_policy'], 'FlociDashboardGetCallerIdentity')
        iam.create_user.assert_called_once_with(UserName='charlie')
        iam.put_user_policy.assert_called_once()
        kwargs = iam.put_user_policy.call_args.kwargs
        self.assertEqual(kwargs['UserName'], 'charlie')
        self.assertEqual(kwargs['PolicyName'], 'FlociDashboardGetCallerIdentity')
        self.assertIn('sts:GetCallerIdentity', kwargs['PolicyDocument'])

    @patch('dashboard.iam_api.FlociClientFactory')
    def test_create_user_helper_can_skip_baseline_policy(self, factory_mock):
        from .iam_api import create_user

        iam = MagicMock()
        iam.create_user.return_value = {'User': {'UserName': 'charlie'}}
        factory_mock.return_value.client.return_value = iam

        result = create_user('charlie', add_baseline_policy=False)

        self.assertEqual(result['baseline_policy'], None)
        iam.create_user.assert_called_once_with(UserName='charlie')
        iam.put_user_policy.assert_not_called()

    @patch('dashboard.iam_views.create_user')
    def test_create_user_endpoint_uses_action_helper(self, create_user):
        create_user.return_value = {
            'user_name': 'charlie',
            'arn': 'arn:aws:iam::000000000000:user/charlie',
            'baseline_policy': 'FlociDashboardGetCallerIdentity',
        }

        response = self.client.post(
            reverse('dashboard:iam-users'),
            data=json.dumps({
                'user_name': 'charlie',
                'add_baseline_policy': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user_name'], 'charlie')
        create_user.assert_called_once_with('charlie', add_baseline_policy=True)

    @patch('dashboard.iam_views.create_user')
    def test_create_user_endpoint_can_skip_baseline_policy(self, create_user):
        create_user.return_value = {
            'user_name': 'charlie',
            'baseline_policy': None,
        }

        response = self.client.post(
            reverse('dashboard:iam-users'),
            data=json.dumps({
                'user_name': 'charlie',
                'add_baseline_policy': False,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_user.assert_called_once_with('charlie', add_baseline_policy=False)

    @patch('dashboard.iam_views.cleanup_user')
    def test_delete_user_endpoint_uses_cleanup_helper(self, cleanup_user):
        cleanup_user.return_value = {'user_name': 'charlie', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:iam-user-detail', kwargs={'user_name': 'charlie'}),
            data=json.dumps({'force': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        cleanup_user.assert_called_once_with('charlie', force=True)

    @patch('dashboard.iam_views.create_group')
    def test_create_group_endpoint_uses_action_helper(self, create_group):
        create_group.return_value = {'group_name': 'developers'}

        response = self.client.post(
            reverse('dashboard:iam-groups'),
            data=json.dumps({'group_name': 'developers'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_group.assert_called_once_with('developers')

    @patch('dashboard.iam_views.cleanup_group')
    def test_delete_group_endpoint_uses_cleanup_helper(self, cleanup_group):
        cleanup_group.return_value = {'group_name': 'developers', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:iam-group-detail', kwargs={'group_name': 'developers'}),
            data=json.dumps({'force': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        cleanup_group.assert_called_once_with('developers', force=True)

    @patch('dashboard.iam_views.create_role')
    def test_create_role_endpoint_uses_action_helper(self, create_role):
        document = {'Version': '2012-10-17', 'Statement': []}
        create_role.return_value = {'role_name': 'app'}

        response = self.client.post(
            reverse('dashboard:iam-roles'),
            data=json.dumps({
                'role_name': 'app',
                'trust_template': 'custom',
                'trust_policy': document,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_role.assert_called_once_with('app', trust_template='custom', trust_policy=document)

    @patch('dashboard.iam_views.cleanup_role')
    def test_delete_role_endpoint_uses_cleanup_helper(self, cleanup_role):
        cleanup_role.return_value = {'role_name': 'app', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:iam-role-detail', kwargs={'role_name': 'app'}),
            data=json.dumps({'force': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        cleanup_role.assert_called_once_with('app', force=True)

    @patch('dashboard.iam_views.create_instance_profile')
    def test_create_instance_profile_endpoint_uses_action_helper(self, create_instance_profile):
        create_instance_profile.return_value = {'instance_profile_name': 'profile'}

        response = self.client.post(
            reverse('dashboard:iam-instance-profiles'),
            data=json.dumps({'instance_profile_name': 'profile'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_instance_profile.assert_called_once_with('profile')

    @patch('dashboard.iam_views.add_role_to_instance_profile')
    def test_add_role_to_instance_profile_endpoint_uses_action_helper(self, add_role_to_instance_profile):
        add_role_to_instance_profile.return_value = {'instance_profile_name': 'profile', 'role_name': 'app'}

        response = self.client.post(
            reverse('dashboard:iam-instance-profile-roles', kwargs={'instance_profile_name': 'profile'}),
            data=json.dumps({'role_name': 'app'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        add_role_to_instance_profile.assert_called_once_with('profile', 'app')

    @patch('dashboard.iam_views.simulate_principal_policy')
    def test_policy_simulation_endpoint_uses_action_helper(self, simulate_principal_policy):
        simulate_principal_policy.return_value = {'supported': True, 'evaluations': []}

        response = self.client.post(
            reverse('dashboard:iam-policy-simulation'),
            data=json.dumps({
                'principal_arn': 'arn:aws:iam::000000000000:user/charlie',
                'action_names': 's3:ListAllMyBuckets',
                'resource_arns': '*',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        simulate_principal_policy.assert_called_once_with(
            'arn:aws:iam::000000000000:user/charlie',
            's3:ListAllMyBuckets',
            '*',
        )

    @patch('dashboard.iam_views.create_access_key')
    def test_create_access_key_endpoint_uses_action_helper(self, create_access_key):
        create_access_key.return_value = {
            'user_name': 'alice',
            'access_key_id': 'AKIAEXAMPLE',
            'secret_access_key': 'secret',
        }

        response = self.client.post(reverse('dashboard:iam-user-access-keys', kwargs={'user_name': 'alice'}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['access_key_id'], 'AKIAEXAMPLE')
        create_access_key.assert_called_once_with('alice')

    @patch('dashboard.iam_views.update_access_key')
    def test_update_access_key_endpoint_uses_action_helper(self, update_access_key):
        update_access_key.return_value = {
            'user_name': 'alice',
            'access_key_id': 'AKIAEXAMPLE',
            'status': 'Inactive',
        }

        response = self.client.put(
            reverse('dashboard:iam-user-access-key-detail', kwargs={
                'user_name': 'alice',
                'access_key_id': 'AKIAEXAMPLE',
            }),
            data=json.dumps({'status': 'Inactive'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'Inactive')
        update_access_key.assert_called_once_with('alice', 'AKIAEXAMPLE', 'Inactive')

    @patch('dashboard.iam_views.delete_access_key')
    def test_delete_access_key_endpoint_uses_action_helper(self, delete_access_key):
        delete_access_key.return_value = {
            'user_name': 'alice',
            'access_key_id': 'AKIAEXAMPLE',
            'deleted': True,
        }

        response = self.client.delete(
            reverse('dashboard:iam-user-access-key-detail', kwargs={
                'user_name': 'alice',
                'access_key_id': 'AKIAEXAMPLE',
            })
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_access_key.assert_called_once_with('alice', 'AKIAEXAMPLE')

    @patch('dashboard.iam_views.assume_role')
    def test_assume_role_endpoint_uses_action_helper(self, assume_role):
        assume_role.return_value = {
            'role_arn': 'arn:aws:iam::000000000000:role/app',
            'session_name': 'dashboard',
            'credentials': {'access_key_id': 'ASIAEXAMPLE'},
        }

        response = self.client.post(
            reverse('dashboard:iam-role-assume', kwargs={'role_name': 'app'}),
            data=json.dumps({
                'role_arn': 'arn:aws:iam::000000000000:role/app',
                'session_name': 'dashboard',
                'duration_seconds': 900,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['credentials']['access_key_id'], 'ASIAEXAMPLE')
        assume_role.assert_called_once_with(
            'arn:aws:iam::000000000000:role/app',
            'dashboard',
            duration_seconds=900,
            session_policy=None,
            session_policy_arns=None,
        )

    @patch('dashboard.iam_views.assume_role')
    def test_assume_role_endpoint_passes_session_policies(self, assume_role):
        assume_role.return_value = {
            'role_arn': 'arn:aws:iam::000000000000:role/app',
            'session_name': 'dashboard',
            'credentials': {'access_key_id': 'ASIAEXAMPLE'},
        }
        session_policy = {
            'Version': '2012-10-17',
            'Statement': [{'Effect': 'Allow', 'Action': 's3:ListAllMyBuckets', 'Resource': '*'}],
        }
        session_policy_arns = [{'arn': 'arn:aws:iam::000000000000:policy/Boundary'}]

        response = self.client.post(
            reverse('dashboard:iam-role-assume', kwargs={'role_name': 'app'}),
            data=json.dumps({
                'role_arn': 'arn:aws:iam::000000000000:role/app',
                'session_name': 'dashboard',
                'session_policy': session_policy,
                'session_policy_arns': session_policy_arns,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        assume_role.assert_called_once_with(
            'arn:aws:iam::000000000000:role/app',
            'dashboard',
            duration_seconds=None,
            session_policy=session_policy,
            session_policy_arns=session_policy_arns,
        )

    @patch('dashboard.iam_views.update_role_trust_policy')
    def test_update_role_trust_policy_endpoint_uses_action_helper(self, update_role_trust_policy):
        update_role_trust_policy.return_value = {'role_name': 'app', 'saved': True}
        document = {'Version': '2012-10-17', 'Statement': []}

        response = self.client.put(
            reverse('dashboard:iam-role-trust-policy', kwargs={'role_name': 'app'}),
            data=json.dumps({'document': document}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['saved'])
        update_role_trust_policy.assert_called_once_with('app', document)

    @patch('dashboard.iam_views.add_user_to_group')
    def test_add_user_to_group_endpoint_uses_action_helper(self, add_user_to_group):
        add_user_to_group.return_value = {'user_name': 'alice', 'group_name': 'admins', 'added': True}

        response = self.client.post(
            reverse('dashboard:iam-group-membership', kwargs={'group_name': 'admins'}),
            data=json.dumps({'user_name': 'alice'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['added'])
        add_user_to_group.assert_called_once_with('alice', 'admins')

    @patch('dashboard.iam_views.remove_user_from_group')
    def test_remove_user_from_group_endpoint_uses_action_helper(self, remove_user_from_group):
        remove_user_from_group.return_value = {'user_name': 'alice', 'group_name': 'admins', 'removed': True}

        response = self.client.delete(
            reverse('dashboard:iam-group-membership', kwargs={'group_name': 'admins'}),
            data=json.dumps({'user_name': 'alice'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['removed'])
        remove_user_from_group.assert_called_once_with('alice', 'admins')

    @patch('dashboard.iam_views.attach_managed_policy')
    def test_attach_managed_policy_endpoint_uses_action_helper(self, attach_managed_policy):
        attach_managed_policy.return_value = {
            'principal_type': 'role',
            'principal_name': 'app',
            'policy_arn': 'arn:aws:iam::000000000000:policy/Local',
            'attached': True,
        }

        response = self.client.post(
            reverse('dashboard:iam-attached-policies', kwargs={
                'principal_type': 'role',
                'principal_name': 'app',
            }),
            data=json.dumps({'policy_arn': 'arn:aws:iam::000000000000:policy/Local'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['attached'])
        attach_managed_policy.assert_called_once_with('role', 'app', 'arn:aws:iam::000000000000:policy/Local')

    @patch('dashboard.iam_views.detach_managed_policy')
    def test_detach_managed_policy_endpoint_uses_action_helper(self, detach_managed_policy):
        detach_managed_policy.return_value = {
            'principal_type': 'role',
            'principal_name': 'app',
            'policy_arn': 'arn:aws:iam::000000000000:policy/Local',
            'detached': True,
        }

        response = self.client.delete(
            reverse('dashboard:iam-attached-policies', kwargs={
                'principal_type': 'role',
                'principal_name': 'app',
            }),
            data=json.dumps({'policy_arn': 'arn:aws:iam::000000000000:policy/Local'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['detached'])
        detach_managed_policy.assert_called_once_with('role', 'app', 'arn:aws:iam::000000000000:policy/Local')

    @patch('dashboard.iam_api.FlociClientFactory')
    def test_put_permissions_boundary_helper_routes_user_and_role_calls(self, factory_mock):
        from .iam_api import put_permissions_boundary

        iam = MagicMock()
        factory_mock.return_value.client.return_value = iam

        user_result = put_permissions_boundary('user', 'alice', 'arn:aws:iam::aws:policy/PowerUserAccess')
        role_result = put_permissions_boundary('role', 'app', 'arn:aws:iam::aws:policy/ReadOnlyAccess')

        self.assertTrue(user_result['saved'])
        self.assertTrue(role_result['saved'])
        iam.put_user_permissions_boundary.assert_called_once_with(
            UserName='alice',
            PermissionsBoundary='arn:aws:iam::aws:policy/PowerUserAccess',
        )
        iam.put_role_permissions_boundary.assert_called_once_with(
            RoleName='app',
            PermissionsBoundary='arn:aws:iam::aws:policy/ReadOnlyAccess',
        )

    @patch('dashboard.iam_api.FlociClientFactory')
    def test_delete_permissions_boundary_helper_routes_user_and_role_calls(self, factory_mock):
        from .iam_api import delete_permissions_boundary

        iam = MagicMock()
        factory_mock.return_value.client.return_value = iam

        user_result = delete_permissions_boundary('user', 'alice')
        role_result = delete_permissions_boundary('role', 'app')

        self.assertTrue(user_result['deleted'])
        self.assertTrue(role_result['deleted'])
        iam.delete_user_permissions_boundary.assert_called_once_with(UserName='alice')
        iam.delete_role_permissions_boundary.assert_called_once_with(RoleName='app')

    @patch('dashboard.iam_views.put_permissions_boundary')
    def test_put_permissions_boundary_endpoint_uses_action_helper(self, put_permissions_boundary):
        put_permissions_boundary.return_value = {
            'principal_type': 'role',
            'principal_name': 'app',
            'policy_arn': 'arn:aws:iam::aws:policy/PowerUserAccess',
            'saved': True,
        }

        response = self.client.put(
            reverse('dashboard:iam-permissions-boundary', kwargs={
                'principal_type': 'role',
                'principal_name': 'app',
            }),
            data=json.dumps({'policy_arn': 'arn:aws:iam::aws:policy/PowerUserAccess'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['saved'])
        put_permissions_boundary.assert_called_once_with(
            'role',
            'app',
            'arn:aws:iam::aws:policy/PowerUserAccess',
        )

    @patch('dashboard.iam_views.delete_permissions_boundary')
    def test_delete_permissions_boundary_endpoint_uses_action_helper(self, delete_permissions_boundary):
        delete_permissions_boundary.return_value = {
            'principal_type': 'role',
            'principal_name': 'app',
            'deleted': True,
        }

        response = self.client.delete(
            reverse('dashboard:iam-permissions-boundary', kwargs={
                'principal_type': 'role',
                'principal_name': 'app',
            }),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_permissions_boundary.assert_called_once_with('role', 'app')

    @patch('dashboard.iam_views.put_inline_policy')
    def test_put_inline_policy_endpoint_uses_action_helper(self, put_inline_policy):
        put_inline_policy.return_value = {
            'principal_type': 'role',
            'principal_name': 'app',
            'policy_name': 'local',
            'saved': True,
        }

        document = {'Version': '2012-10-17', 'Statement': []}
        response = self.client.put(
            reverse('dashboard:iam-inline-policy-detail', kwargs={
                'principal_type': 'role',
                'principal_name': 'app',
                'policy_name': 'local',
            }),
            data=json.dumps({'document': document}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['saved'])
        put_inline_policy.assert_called_once_with('role', 'app', 'local', document)

    @patch('dashboard.iam_views.create_managed_policy')
    def test_create_managed_policy_endpoint_uses_action_helper(self, create_managed_policy):
        create_managed_policy.return_value = {
            'policy_name': 'Local',
            'policy_arn': 'arn:aws:iam::000000000000:policy/Local',
        }

        document = {'Version': '2012-10-17', 'Statement': []}
        response = self.client.post(
            reverse('dashboard:iam-managed-policies'),
            data=json.dumps({
                'name': 'Local',
                'document': document,
                'description': 'Local test policy',
                'path': '/service-role/',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['policy_name'], 'Local')
        create_managed_policy.assert_called_once_with(
            'Local',
            document,
            description='Local test policy',
            path='/service-role/',
        )

    @patch('dashboard.iam_views.create_policy_version')
    def test_create_policy_version_endpoint_uses_action_helper(self, create_policy_version):
        create_policy_version.return_value = {'arn': 'arn:aws:iam::000000000000:policy/Local', 'version_id': 'v2'}
        document = {'Version': '2012-10-17', 'Statement': []}

        response = self.client.post(
            reverse('dashboard:iam-managed-policy-versions'),
            data=json.dumps({
                'policy_arn': 'arn:aws:iam::000000000000:policy/Local',
                'document': document,
                'set_as_default': False,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['version_id'], 'v2')
        create_policy_version.assert_called_once_with(
            'arn:aws:iam::000000000000:policy/Local',
            document,
            set_as_default=False,
        )

    @patch('dashboard.iam_views.set_default_policy_version')
    def test_set_default_policy_version_endpoint_uses_action_helper(self, set_default_policy_version):
        set_default_policy_version.return_value = {
            'arn': 'arn:aws:iam::000000000000:policy/Local',
            'version_id': 'v2',
            'default': True,
        }

        response = self.client.put(
            reverse('dashboard:iam-managed-policy-version-detail'),
            data=json.dumps({
                'policy_arn': 'arn:aws:iam::000000000000:policy/Local',
                'version_id': 'v2',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['default'])
        set_default_policy_version.assert_called_once_with('arn:aws:iam::000000000000:policy/Local', 'v2')

    @patch('dashboard.iam_views.delete_policy_version')
    def test_delete_policy_version_endpoint_uses_action_helper(self, delete_policy_version):
        delete_policy_version.return_value = {
            'arn': 'arn:aws:iam::000000000000:policy/Local',
            'version_id': 'v1',
            'deleted': True,
        }

        response = self.client.delete(
            reverse('dashboard:iam-managed-policy-version-detail'),
            data=json.dumps({
                'policy_arn': 'arn:aws:iam::000000000000:policy/Local',
                'version_id': 'v1',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_policy_version.assert_called_once_with('arn:aws:iam::000000000000:policy/Local', 'v1')

    @patch('dashboard.iam_views.save_login_profile')
    def test_create_login_profile_endpoint(self, save_login_profile):
        save_login_profile.return_value = {'user_name': 'alice', 'password_reset_required': True}
        response = self.client.post(
            reverse('dashboard:iam-user-login-profile', kwargs={'user_name': 'alice'}),
            data=json.dumps({'password': 'LocalPassword1!', 'password_reset_required': True}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        save_login_profile.assert_called_once_with('alice', 'LocalPassword1!', reset_required=True, update=False)

    @patch('dashboard.iam_views.get_access_key_last_used')
    def test_access_key_last_used_endpoint(self, get_last_used):
        get_last_used.return_value = {'access_key_id': 'AKIALOCAL', 'last_used': {}}
        response = self.client.get(reverse('dashboard:iam-access-key-last-used', kwargs={'access_key_id': 'AKIALOCAL'}))
        self.assertEqual(response.status_code, 200)
        get_last_used.assert_called_once_with('AKIALOCAL')

    @patch('dashboard.iam_views.delete_instance_profile')
    def test_delete_instance_profile_endpoint(self, delete_profile):
        delete_profile.return_value = {'instance_profile_name': 'app', 'deleted': True}
        response = self.client.delete(reverse('dashboard:iam-instance-profile-detail', kwargs={'instance_profile_name': 'app'}))
        self.assertEqual(response.status_code, 200)
        delete_profile.assert_called_once_with('app')

    @patch('dashboard.iam_views.update_tags')
    def test_tag_iam_resource_endpoint(self, update_tags):
        update_tags.return_value = {'resource_type': 'user', 'resource_name': 'alice'}
        response = self.client.post(
            reverse('dashboard:iam-tags', kwargs={'resource_type': 'user', 'resource_name': 'alice'}),
            data=json.dumps({'tags': {'team': 'platform'}}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        update_tags.assert_called_once_with('user', 'alice', {'team': 'platform'}, remove=None)

    @patch('dashboard.iam_views.delete_managed_policy')
    def test_delete_managed_policy_endpoint(self, delete_policy):
        arn = 'arn:aws:iam::000000000000:policy/Local'
        delete_policy.return_value = {'policy_arn': arn, 'deleted': True}
        response = self.client.delete(reverse('dashboard:iam-managed-policies'), data=json.dumps({'policy_arn': arn}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        delete_policy.assert_called_once_with(arn)

    def test_iam_console_exposes_complete_lifecycle_controls(self):
        source = (Path(__file__).resolve().parent / 'static' / 'dashboard' / 'iam-console.js').read_text()
        for label in ('Update user', 'Create login', 'Update role', 'Delete policy', 'Delete profile', 'Edit tags', 'Last used'):
            self.assertIn(label, source)


class GuidedEC2LabTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    def test_ec2_lab_catalog_includes_all_guided_workflows(self):
        from dashboard.labs import labs_for_service

        labs = {lab['key']: lab for lab in labs_for_service('ec2')}
        expected = {
            'guided-imds', 'guided-userdata', 'guided-instance-role',
            'guided-web-server', 'guided-broken-route',
            'guided-private-s3', 'guided-ssm-command',
        }

        self.assertTrue(expected.issubset(labs))
        self.assertTrue(all(labs[key]['guided'] for key in expected))
        self.assertTrue(all(len(labs[key]['steps']) >= 3 for key in expected))

    @patch('dashboard.labs.ec2_guided.RUNNERS')
    def test_guided_lab_step_uses_runner_and_tracks_duration(self, runners):
        from dashboard.labs.ec2_guided import run_guided_step

        runners.__contains__.return_value = True
        runners.__getitem__.return_value = {
            'launch-instance': lambda: {
                'service': 'ec2', 'lab': 'guided-imds', 'verified': True,
            }
        }

        result = run_guided_step('guided-imds', 'launch-instance')

        self.assertTrue(result['verified'])
        self.assertGreaterEqual(result['duration_ms'], 0)

    def test_guided_status_reflects_cached_completion(self):
        from django.core.cache import cache
        from dashboard.labs.ec2_guided import guided_status

        pending = guided_status('guided-ssm-command')
        cache.set('floci-lab:ec2:guided:guided-ssm-command:launch-managed-instance', True)
        cache.set('floci-lab:ec2:guided:guided-ssm-command:send-ssm-command', True)
        cache.set('floci-lab:ec2:guided:guided-ssm-command:get-command-invocation', True)
        complete = guided_status('guided-ssm-command')

        self.assertFalse(pending['complete'])
        self.assertTrue(complete['complete'])
        self.assertTrue(complete['steps']['launch-managed-instance']['verified'])

    @patch('dashboard.labs.ec2_guided._ec2')
    def test_reset_clears_progress_without_resources(self, ec2_client):
        from django.core.cache import cache
        from dashboard.labs.ec2_guided import reset_guided_lab

        cache.set('floci-lab:ec2:guided:guided-ssm-command:launch-managed-instance', True)
        result = reset_guided_lab('guided-ssm-command')

        self.assertTrue(result['reset'])
        self.assertIsNone(cache.get('floci-lab:ec2:guided:guided-ssm-command:launch-managed-instance'))
        ec2_client.assert_called_once_with()

    def test_guided_lab_page_exposes_workflow(self):
        response = self.client.get(
            reverse('dashboard:service-labs', kwargs={'service_key': 'ec2'}),
            {'lab': 'guided-imds'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Launch an instance and inspect IMDS')
        self.assertContains(response, 'data-step-key="launch-instance"')

    def test_imds_command_bounds_network_wait_and_bypasses_proxy(self):
        from dashboard.labs.ec2_guided import _imds_get_command

        command = _imds_get_command('/latest/meta-data/instance-id')

        self.assertIn('http://169.254.169.254/latest/meta-data/instance-id', command)
        self.assertIn('--max-time 2', command)
        self.assertIn('--noproxy "*"', command)

    def test_http_command_supports_guest_web_server(self):
        from dashboard.labs.ec2_guided import _http_get_command

        command = _http_get_command('127.0.0.1', 8080, '/')

        self.assertIn('http://127.0.0.1:8080/', command)
        self.assertIn('--connect-timeout 1 --max-time 2', command)

    @patch('dashboard.labs.ec2_guided.time.sleep')
    @patch('dashboard.labs.ec2_guided._execute')
    def test_userdata_output_waits_for_async_guest_execution(self, execute, sleep):
        from dashboard.labs.ec2_guided import _wait_for_output

        execute.side_effect = [
            {'status': 'Failed', 'stdout': '', 'stderr': 'not found'},
            {'status': 'Success', 'stdout': 'floci-userdata-ok\n', 'stderr': ''},
        ]

        result = _wait_for_output('i-123', 'cat /tmp/floci-userdata.txt', 'floci-userdata-ok')

        self.assertEqual(result['status'], 'Success')
        self.assertEqual(execute.call_count, 2)
        sleep.assert_called_once_with(0.5)

    @patch('dashboard.ec2_api._ec2_client')
    def test_run_instances_leaves_userdata_encoding_to_botocore(self, ec2_client):
        from dashboard.ec2_api import run_instances

        ec2_client.return_value.run_instances.return_value = {
            'ReservationId': 'r-123',
            'Instances': [{'InstanceId': 'i-123'}],
        }
        script = '#!/bin/sh\necho floci-userdata-ok\n'

        run_instances('ami-ubuntu2204', 't2.micro', user_data=script)

        self.assertEqual(
            ec2_client.return_value.run_instances.call_args.kwargs['UserData'],
            script,
        )

    def test_cloudscape_app_page_renders_spa_root(self):
        response = self.client.get(reverse('dashboard:cloudscape-app'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="root"')
        self.assertContains(response, 'app.js')
        self.assertContains(response, 'main.css')

    def test_api_services_catalog_returns_json(self):
        response = self.client.get(reverse('dashboard:services'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('services', data)
        self.assertGreaterEqual(len(data['services']), 65)


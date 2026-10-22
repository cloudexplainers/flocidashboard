import React, { useState, useEffect, useRef } from 'react';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import ProgressBar from '@cloudscape-design/components/progress-bar';
import Tabs from '@cloudscape-design/components/tabs';
import TextFilter from '@cloudscape-design/components/text-filter';
import Cards from '@cloudscape-design/components/cards';
import Badge from '@cloudscape-design/components/badge';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Modal from '@cloudscape-design/components/modal';
import Alert from '@cloudscape-design/components/alert';
import SegmentedControl from '@cloudscape-design/components/segmented-control';
import { fetchLabExams, fetchLabsCatalog, fetchLabsProgress, fetchLabStatus, runLabStep, resetLab, resetAllLabs } from '../api/client';
import { ConceptCard, ExamTracksResponse, LabDefinition, LabExamTag, LabStep } from '../types';
import { ExamTrackView, examTagLabel } from '../components/ExamTrackView';
import { CodeSnippet } from '../components/CodeSnippet';
import { LAB_CURRICULUM, FLAT_CURRICULUM_LABS, LAB_COUNT, getCurriculumItem, getLevelForLab } from '../labCurriculum';
import { LabCompletionMap, readLabCompletion, writeLabCompletion } from '../labCompletion';

const TRACK_STORAGE_KEY = 'floci_labs_study_track';
const LEVELS_TRACK = 'levels';

function initialTrack(): string {
  const fromUrl = new URLSearchParams(window.location.search).get('track');
  if (fromUrl) return fromUrl;
  try {
    return localStorage.getItem(TRACK_STORAGE_KEY) || LEVELS_TRACK;
  } catch {
    return LEVELS_TRACK;
  }
}

/** Primary tags first, then by exam and task. */
function sortedTags(tags: LabExamTag[] = []): LabExamTag[] {
  return [...tags].sort(
    (a, b) => Number(b.primary) - Number(a.primary) || a.exam.localeCompare(b.exam) || a.task.localeCompare(b.task, undefined, { numeric: true }),
  );
}

export const LabsConsole: React.FC = () => {
  const [allLabsList, setAllLabsList] = useState<LabDefinition[]>([]);
  const [examData, setExamData] = useState<ExamTracksResponse | null>(null);
  const [examError, setExamError] = useState<string | null>(null);
  const [track, setTrackState] = useState<string>(initialTrack);

  const setTrack = (next: string) => {
    setTrackState(next);
    try {
      localStorage.setItem(TRACK_STORAGE_KEY, next);
    } catch {
      // ignore
    }
    const url = new URL(window.location.href);
    if (next === LEVELS_TRACK) url.searchParams.delete('track');
    else url.searchParams.set('track', next);
    window.history.replaceState(null, '', url.pathname + url.search);
  };
  const [completedLabsMap, setCompletedLabsMap] = useState<LabCompletionMap>(readLabCompletion);

  // Persist completion and let the sidebar know whenever it changes.
  useEffect(() => {
    writeLabCompletion(completedLabsMap);
  }, [completedLabsMap]);

  const saveCompletedLabState = (service: string, key: string, isComplete: boolean) => {
    setCompletedLabsMap((prev) => {
      return { ...prev, [`${service}:${key}`]: isComplete };
    });
  };

  const [activeLab, setActiveLab] = useState<LabDefinition | null>(null);
  const [loading, setLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [labError, setLabError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState('');
  const [selectedLevelId, setSelectedLevelId] = useState<string>('all');
  const [runningStepKey, setRunningStepKey] = useState<string | null>(null);
  const [isRunningAll, setIsRunningAll] = useState(false);
  const [activeSdk, setActiveSdkState] = useState<'cli' | 'boto3' | 'terraform'>(() => {
    try {
      const saved = localStorage.getItem('floci_labs_active_sdk');
      if (saved === 'cli' || saved === 'boto3' || saved === 'terraform') {
        return saved;
      }
    } catch {
      // ignore
    }
    return 'cli';
  });

  const handleSdkChange = (sdk: 'cli' | 'boto3' | 'terraform') => {
    setActiveSdkState(sdk);
    try {
      localStorage.setItem('floci_labs_active_sdk', sdk);
    } catch {
      // ignore
    }
  };

  const [stepOutputs, setStepOutputs] = useState<Record<string, { status: string; body: string }>>({});
  const [stepCompleted, setStepCompleted] = useState<Record<string, boolean>>({});

  const cancelRunAllRef = useRef(false);
  const labRequestId = useRef(0);
  const stepRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const loadCatalog = async () => {
    setLoading(true);
    setCatalogError(null);
    try {
      const data = await fetchLabsCatalog();
      const flattened = (data.services || []).flatMap((s) => s.labs);
      setAllLabsList(flattened);

      setExamError(null);
      fetchLabExams()
        .then(setExamData)
        .catch((err) => setExamError(err?.message || 'Could not load the exam study tracks.'));

      // Check URL query parameters for direct lab link
      const params = new URLSearchParams(window.location.search);
      const urlLabKey = params.get('lab');
      const urlServiceKey = params.get('service');
      if (urlLabKey) {
        const found = flattened.find((l) => l.key === urlLabKey && (!urlServiceKey || l.service === urlServiceKey));
        if (found) {
          openLab(found, false);
        }
      }

      // Background fetch of completion status for all labs
      fetchLabsProgress().then((prog) => {
        setCompletedLabsMap((prev) => {
          const map: Record<string, boolean> = { ...prev };
          (prog.labs || []).forEach((l) => {
            const labKey = l.key || (l as any).lab;
            if (labKey) {
              map[`${l.service}:${labKey}`] = Boolean(l.complete);
            }
          });
          return map;
        });
      });
    } catch (err: any) {
      console.error(err);
      setCatalogError(err?.message || 'Could not load the lab catalog.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCatalog();
  }, []);

  // Listen for navigation events from SideNav or history changes
  useEffect(() => {
    const handleUrlSync = () => {
      const params = new URLSearchParams(window.location.search);
      const urlLabKey = params.get('lab');
      const urlServiceKey = params.get('service');

      if (urlLabKey && allLabsList.length > 0) {
        const found = allLabsList.find((l) => l.key === urlLabKey && (!urlServiceKey || l.service === urlServiceKey));
        if (found) {
          openLab(found, false);
        }
      } else if (!urlLabKey && activeLab) {
        closeLab(false);
      }
    };

    window.addEventListener('popstate', handleUrlSync);
    window.addEventListener('floci_navigate', handleUrlSync);
    return () => {
      window.removeEventListener('popstate', handleUrlSync);
      window.removeEventListener('floci_navigate', handleUrlSync);
    };
  }, [allLabsList, activeLab]);

  const openLab = async (lab: LabDefinition, updateUrl = true) => {
    cancelRunAllRef.current = true;
    const requestId = ++labRequestId.current;
    setIsRunningAll(false);
    setRunningStepKey(null);
    setLabError(null);
    setActiveLab(lab);
    setStepOutputs({});
    setStepCompleted({});
    setStatusLoading(true);

    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set('lab', lab.key);
      url.searchParams.set('service', lab.service);
      window.history.pushState(null, '', url.pathname + url.search);
    }

    try {
      const statusData = await fetchLabStatus(lab.service, lab.key);
      if (requestId !== labRequestId.current) return;
      if (statusData.error) throw new Error(statusData.error);
      const completedMap: Record<string, boolean> = {};
      const outputMap: Record<string, { status: string; body: string }> = {};

      Object.entries(statusData.steps || {}).forEach(([k, v]) => {
        completedMap[k] = Boolean(v.verified);
        if (v.verification) {
          outputMap[k] = {
            status: v.verified ? 'Verified' : 'Unverified',
            body: v.verification.message || JSON.stringify(v.verification, null, 2),
          };
        }
      });
      setStepCompleted(completedMap);
      setStepOutputs(outputMap);

      saveCompletedLabState(lab.service, lab.key, Boolean(statusData.complete));
    } catch (err: any) {
      if (requestId === labRequestId.current) setLabError(err.message || 'Failed to load lab status');
    } finally {
      if (requestId === labRequestId.current) setStatusLoading(false);
    }

    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const closeLab = (updateUrl = true) => {
    ++labRequestId.current;
    cancelRunAllRef.current = true;
    setIsRunningAll(false);
    setActiveLab(null);
    setStepOutputs({});
    setStepCompleted({});

    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.delete('lab');
      url.searchParams.delete('service');
      window.history.pushState(null, '', url.pathname + url.search);
    }
  };

  const scrollToStep = (stepKey: string) => {
    const el = stepRefs.current[stepKey];
    if (el) {
      const rect = el.getBoundingClientRect();
      const offsetTop = window.pageYOffset + rect.top - 14;
      window.scrollTo({ top: Math.max(0, offsetTop), behavior: 'smooth' });
    }
  };

  const handleRunStep = async (step: LabStep): Promise<boolean> => {
    if (!activeLab) return false;
    const requestId = labRequestId.current;
    setLabError(null);
    setRunningStepKey(step.key);

    try {
      const data = await runLabStep(activeLab.service, activeLab.key, step.key);
      if (requestId !== labRequestId.current) return false;
      const isVerified = Boolean(data.verified);
      const outputBody = data.stdout || JSON.stringify(data.verification || data.json || data, null, 2);

      setStepOutputs((prev) => ({
        ...prev,
        [step.key]: {
          status: isVerified ? 'Verified' : 'Not verified',
          body: outputBody,
        },
      }));
      setStepCompleted((prev) => {
        const next = { ...prev, [step.key]: isVerified };
        const allDone = activeLab.steps?.every((s) => s.key === step.key ? isVerified : next[s.key]);
        if (allDone) {
          saveCompletedLabState(activeLab.service, activeLab.key, true);
        }
        return next;
      });
      return isVerified;
    } catch (err: any) {
      if (requestId !== labRequestId.current) return false;
      setStepOutputs((prev) => ({
        ...prev,
        [step.key]: {
          status: 'Failed',
          body: err.message || 'Execution error',
        },
      }));
      return false;
    } finally {
      if (requestId === labRequestId.current) setRunningStepKey(null);
    }
  };

  const handleRunAllSteps = async () => {
    if (!activeLab || isRunningAll) return;
    setIsRunningAll(true);
    cancelRunAllRef.current = false;
    const requestId = labRequestId.current;

    const steps = activeLab.steps || [];
    for (let i = 0; i < steps.length; i++) {
      if (cancelRunAllRef.current || requestId !== labRequestId.current) break;
      const step = steps[i];
      if (stepCompleted[step.key]) continue; // Skip already verified steps

      // 1. Smoothly scroll to the target step and let the camera settle
      scrollToStep(step.key);
      await new Promise((resolve) => setTimeout(resolve, 500));
      if (cancelRunAllRef.current || requestId !== labRequestId.current) break;

      // 2. Execute step
      const verified = await handleRunStep(step);
      if (!verified) {
        if (requestId === labRequestId.current) setLabError('Run all stopped because this step failed or could not be verified. Review its result and retry.');
        break;
      }

      // 3. Keep step visible and readable before moving to the next step
      await new Promise((resolve) => setTimeout(resolve, 1400));
    }

    if (requestId === labRequestId.current) setIsRunningAll(false);
  };

  const handleResetLab = async () => {
    if (!activeLab) return;
    const requestId = ++labRequestId.current;
    setLabError(null);
    cancelRunAllRef.current = true;
    setIsRunningAll(false);
    setStatusLoading(true);
    try {
      await resetLab(activeLab.service, activeLab.key);
      if (requestId !== labRequestId.current) return;
      setStepCompleted({});
      setStepOutputs({});
      saveCompletedLabState(activeLab.service, activeLab.key, false);
    } catch (err: any) {
      if (requestId === labRequestId.current) setLabError(err.message || 'Failed to reset lab');
    } finally {
      if (requestId === labRequestId.current) setStatusLoading(false);
    }
  };

  const [resetAllModalOpen, setResetAllModalOpen] = useState(false);
  const [resettingAll, setResettingAll] = useState(false);
  const [resetResultMessage, setResetResultMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const handleResetAllLabs = async () => {
    setResettingAll(true);
    setResetResultMessage(null);
    try {
      const res = await resetAllLabs();
      setCompletedLabsMap({});
      setResetResultMessage({
        type: 'success',
        text: `Successfully reset all ${res.reset_lab_count || LAB_COUNT} labs in the system.`,
      });
      setResetAllModalOpen(false);
      await loadCatalog();
    } catch (err: any) {
      setResetResultMessage({
        type: 'error',
        text: err.message || 'Failed to reset all labs',
      });
    } finally {
      setResettingAll(false);
    }
  };

  // Sort labs in curriculum order
  const sortedCurriculumLabs = [...allLabsList].sort((a, b) => {
    const curA = getCurriculumItem(a.service, a.key);
    const curB = getCurriculumItem(b.service, b.key);
    const idA = curA ? curA.id : 999;
    const idB = curB ? curB.id : 999;
    return idA - idB;
  });

  // Filter by level and search text
  const filteredLabs = sortedCurriculumLabs.filter((l) => {
    const curItem = getCurriculumItem(l.service, l.key);
    const levelMatch = selectedLevelId === 'all' || curItem?.levelId === selectedLevelId;
    const textMatch =
      !filterText ||
      l.title.toLowerCase().includes(filterText.toLowerCase()) ||
      l.service.toLowerCase().includes(filterText.toLowerCase()) ||
      l.description.toLowerCase().includes(filterText.toLowerCase()) ||
      (l.exams || []).some((tag) => examTagLabel(tag).toLowerCase().includes(filterText.toLowerCase())) ||
      String(curItem?.id).includes(filterText);
    return levelMatch && textMatch;
  });

  // Exam study tracks
  const selectedExam = examData?.exams.find((exam) => exam.code === track);
  // An unknown ?track= value falls back to the level view once exams have loaded.
  const showExamTrack = track !== LEVELS_TRACK && (Boolean(selectedExam) || (!examData && !examError) || Boolean(examError));
  const conceptsByKey: Record<string, ConceptCard> = Object.fromEntries((examData?.concepts || []).map((card) => [card.key, card]));
  const openLabByKey = (service: string, key: string) => {
    const lab = allLabsList.find((item) => item.service === service && item.key === key);
    if (lab) openLab(lab);
  };

  // Active lab curriculum metadata
  const currentCurriculum = activeLab ? getCurriculumItem(activeLab.service, activeLab.key) : undefined;
  const currentLevel = activeLab ? getLevelForLab(activeLab.service, activeLab.key) : undefined;

  // Next and Previous labs in curriculum
  const currentCurriculumIndex = currentCurriculum ? currentCurriculum.id : -1;
  const prevCurriculumItem = currentCurriculumIndex > 1 ? FLAT_CURRICULUM_LABS[currentCurriculumIndex - 2] : undefined;
  const nextCurriculumItem = currentCurriculumIndex > 0 && currentCurriculumIndex < FLAT_CURRICULUM_LABS.length ? FLAT_CURRICULUM_LABS[currentCurriculumIndex] : undefined;

  const navigateToCurriculumItem = (item: typeof FLAT_CURRICULUM_LABS[0]) => {
    const found = allLabsList.find((l) => l.service === item.service && l.key === item.key);
    if (found) {
      openLab(found);
    }
  };

  const completedCount = activeLab?.steps?.filter((s) => stepCompleted[s.key] || s.status?.verified).length || 0;
  const totalCount = activeLab?.steps?.length || 1;
  const percentComplete = Math.round((completedCount / totalCount) * 100);
  const isLabComplete = (completedCount === totalCount && totalCount > 0) || Boolean(completedLabsMap[`${activeLab?.service}:${activeLab?.key}`]);

  // If a lab is open, show the active Lab Studio
  if (activeLab) {
    return (
      <SpaceBetween size="l">
        {labError && <Alert type="error">{labError}</Alert>}
        <Container
          header={
            <Header
              variant="h1"
              description={activeLab.description}
              actions={
                <SpaceBetween direction="horizontal" size="xs">
                  <Button onClick={() => closeLab()}>← Back to Labs Directory</Button>
                  <Button onClick={handleResetLab} loading={statusLoading} disabled={isRunningAll || runningStepKey !== null}>
                    Reset lab
                  </Button>
                  <Button
                    variant="primary"
                    iconName="caret-right-filled"
                    onClick={handleRunAllSteps}
                    loading={isRunningAll}
                    disabled={isLabComplete || statusLoading || runningStepKey !== null}
                  >
                    {isLabComplete ? '✓ Lab Fully Completed' : isRunningAll ? 'Running steps...' : '▶ Run all remaining steps'}
                  </Button>
                </SpaceBetween>
              }
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                {currentCurriculum && (
                  <Badge color="grey">Lesson #{currentCurriculum.id} of {LAB_COUNT}</Badge>
                )}
                {currentLevel && (
                  <Badge color={currentLevel.color}>{currentLevel.badge}</Badge>
                )}
                <Badge color="blue">{activeLab.service.toUpperCase()}</Badge>
                {sortedTags(activeLab.exams).map((tag) => (
                  <span key={`${tag.exam}-${tag.task}`} title={`${tag.exam} task ${tag.task}${tag.primary ? ' (main focus)' : ''}`}>
                    <Badge color={tag.primary ? 'green' : 'grey'}>{examTagLabel(tag)}</Badge>
                  </span>
                ))}
                <span style={{ fontSize: '18px', fontWeight: 600 }}>{activeLab.title}</span>
                {isLabComplete && <StatusIndicator type="success">Completed</StatusIndicator>}
              </div>
            </Header>
          }
        >
          <ProgressBar
            value={percentComplete}
            label="Lab Step Completion"
            description={`${completedCount} of ${totalCount} steps complete (${percentComplete}%)`}
            status={percentComplete === 100 ? 'success' : 'in-progress'}
          />
        </Container>

        <SpaceBetween size="m">
          <style>{`
            .lab-step-completed > div {
              border-color: #1d8102 !important;
              box-shadow: 0 0 0 1px #1d8102 !important;
            }
          `}</style>
          {activeLab.steps?.map((step, idx) => {
            const isDone = Boolean(stepCompleted[step.key] || step.status?.verified);
            const output = stepOutputs[step.key];

            return (
              <div
                key={step.key}
                ref={(el) => { stepRefs.current[step.key] = el; }}
                className={isDone ? 'lab-step-completed' : 'lab-step-pending'}
                style={{
                  borderRadius: '16px',
                  transition: 'all 0.2s ease',
                }}
              >
                <Container
                  header={
                    <Header
                      variant="h3"
                      actions={
                        <SpaceBetween direction="horizontal" size="xs">
                          {isDone && <StatusIndicator type="success">Verified</StatusIndicator>}
                          <Button
                            variant={isDone ? 'normal' : 'primary'}
                            iconName={isDone ? 'status-positive' : 'caret-right-filled'}
                            onClick={() => handleRunStep(step)}
                            loading={runningStepKey === step.key}
                            disabled={isDone || isRunningAll || statusLoading || runningStepKey !== null}
                          >
                            {isDone ? '✓ Step Complete' : 'Run step'}
                          </Button>
                        </SpaceBetween>
                      }
                    >
                      Step {idx + 1}: {step.title}
                    </Header>
                  }
                >
                  <SpaceBetween size="m">
                    <Tabs
                      activeTabId={activeSdk}
                      onChange={({ detail }) => handleSdkChange(detail.activeTabId as any)}
                      tabs={[
                        {
                          label: 'AWS CLI',
                          id: 'cli',
                          content: (
                            <CodeSnippet language="cli" code={step.command} />
                          ),
                        },
                        {
                          label: 'Python (boto3)',
                          id: 'boto3',
                          content: (
                            <CodeSnippet
                              language="boto3"
                              code={step.snippets?.boto3 || step.command}
                            />
                          ),
                        },
                        {
                          label: 'Terraform (HCL)',
                          id: 'terraform',
                          content: (
                            <CodeSnippet
                              language="terraform"
                              code={step.snippets?.terraform || step.command}
                            />
                          ),
                        },
                      ]}
                    />

                    <ExpandableSection headerText="Explain command & concept">
                      <p style={{ lineHeight: '1.6', margin: 0 }}>{step.explanation}</p>
                    </ExpandableSection>

                    {output && (
                      <Container
                        header={
                          <Header variant="h3">
                            <StatusIndicator type={output.status === 'Failed' ? 'error' : output.status === 'Not verified' ? 'warning' : 'success'}>
                              Execution Result: {output.status}
                            </StatusIndicator>
                          </Header>
                        }
                      >
                        <CodeSnippet language="json" code={output.body} />
                      </Container>
                    )}
                  </SpaceBetween>
                </Container>
              </div>
            );
          })}
        </SpaceBetween>

        {/* Next / Previous Curriculum Navigation */}
        <Container>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
            {prevCurriculumItem ? (
              <Button onClick={() => navigateToCurriculumItem(prevCurriculumItem)}>
                ← Previous: #{prevCurriculumItem.id}. [{prevCurriculumItem.service.toUpperCase()}] {prevCurriculumItem.title}
              </Button>
            ) : (
              <div />
            )}
            {nextCurriculumItem && (
              <Button variant="primary" onClick={() => navigateToCurriculumItem(nextCurriculumItem)}>
                Next: #{nextCurriculumItem.id}. [{nextCurriculumItem.service.toUpperCase()}] {nextCurriculumItem.title} →
              </Button>
            )}
          </div>
        </Container>
      </SpaceBetween>
    );
  }

  // Otherwise, render the instant Labs Directory view
  return (
    <SpaceBetween size="l">
      {resetResultMessage && (
        <Alert
          type={resetResultMessage.type}
          dismissible
          onDismiss={() => setResetResultMessage(null)}
        >
          {resetResultMessage.text}
        </Alert>
      )}

      <Container
        header={
          <Header
            variant="h1"
            description={`Learn AWS hands-on, from foundations to private networking and platform engineering, across ${LAB_COUNT} lessons in ${LAB_CURRICULUM.length} progressive levels.`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="normal"
                  iconName="remove"
                  onClick={() => setResetAllModalOpen(true)}
                  loading={resettingAll}
                >
                  Reset All Labs
                </Button>
                <Button iconName="refresh" onClick={loadCatalog} loading={loading}>
                  Refresh catalog
                </Button>
              </SpaceBetween>
            }
          >
            AWS Workflow Labs Curriculum ({LAB_COUNT} Progressive Lessons)
          </Header>
        }
      >
        <SpaceBetween size="m">
          <div style={{ overflowX: 'auto', paddingBottom: '4px' }}>
            <SegmentedControl
              label="Study track"
              selectedId={showExamTrack ? track : LEVELS_TRACK}
              onChange={({ detail }) => setTrack(detail.selectedId)}
              options={[
                { id: LEVELS_TRACK, text: 'Levels' },
                ...(examData?.exams || []).map((exam) => ({ id: exam.code, text: `${exam.code} ${exam.short_title}` })),
              ]}
            />
          </div>

          {showExamTrack ? (
            selectedExam ? (
              <ExamTrackView exam={selectedExam} concepts={conceptsByKey} completed={completedLabsMap} onOpenLab={openLabByKey} />
            ) : examError ? (
              <Alert type="error" action={<Button onClick={loadCatalog}>Retry</Button>}>
                Could not load the exam study tracks: {examError}
              </Alert>
            ) : (
              <StatusIndicator type="loading">Loading exam study track</StatusIndicator>
            )
          ) : (
            <>
              {/* Level Filter Segmented Control */}
              <div style={{ overflowX: 'auto', paddingBottom: '4px' }}>
                <SegmentedControl
                  selectedId={selectedLevelId}
                  onChange={({ detail }) => setSelectedLevelId(detail.selectedId)}
                  options={[
                    { id: 'all', text: `All ${LAB_COUNT} Labs` },
                    ...LAB_CURRICULUM.map((lvl) => ({
                      id: lvl.id,
                      text: `${lvl.title} (${lvl.labs.length})`,
                    })),
                  ]}
                />
              </div>

              <TextFilter
                filteringText={filterText}
                filteringPlaceholder={`Filter labs by number (#1-#${LAB_COUNT}), service (S3, IAM, EC2, EKS), exam task (SAA 2.1), or keyword...`}
                onChange={({ detail }) => setFilterText(detail.filteringText)}
              />

              <Cards
                cardDefinition={{
                  header: (item) => {
                    const isComplete = Boolean(completedLabsMap[`${item.service}:${item.key}`]);
                    const curItem = getCurriculumItem(item.service, item.key);
                    const curLevel = getLevelForLab(item.service, item.key);

                    return (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                          {curItem && (
                            <Badge color="grey">#{curItem.id}</Badge>
                          )}
                          {curLevel && (
                            <Badge color={curLevel.color}>{curLevel.badge}</Badge>
                          )}
                          <Badge color="blue">{item.service.toUpperCase()}</Badge>
                          {sortedTags(item.exams).filter((tag) => tag.primary).map((tag) => (
                            <Badge key={`${tag.exam}-${tag.task}`} color="grey">{examTagLabel(tag)}</Badge>
                          ))}
                          {isComplete && <Badge color="green">✓ COMPLETE</Badge>}
                        </div>
                        <Button variant="inline-link" onClick={() => openLab(item)}>
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '15px' }}>
                            {isComplete && <StatusIndicator type="success" />}
                            <strong style={{ color: isComplete ? '#4ec9b0' : 'inherit' }}>{item.title}</strong>
                          </span>
                        </Button>
                      </div>
                    );
                  },
                  sections: [
                    {
                      id: 'description',
                      content: (item) => {
                        const isComplete = Boolean(completedLabsMap[`${item.service}:${item.key}`]);
                        return (
                          <div
                            style={{
                              borderRadius: '6px',
                              padding: isComplete ? '8px 10px' : undefined,
                              border: isComplete ? '1px solid #1d8102' : undefined,
                              background: isComplete ? 'rgba(29, 129, 2, 0.08)' : undefined,
                            }}
                          >
                            {item.description}
                          </div>
                        );
                      },
                    },
                    {
                      id: 'steps',
                      header: 'Runnable Steps',
                      content: (item) => {
                        const count = item.step_count || item.steps?.length || 1;
                        return `${count} ${count === 1 ? 'step' : 'steps'} (CLI / Boto3 / Terraform)`;
                      },
                    },
                    {
                      id: 'action',
                      content: (item) => {
                        const isComplete = Boolean(completedLabsMap[`${item.service}:${item.key}`]);
                        return (
                          <Button
                            variant={isComplete ? 'normal' : 'primary'}
                            iconName={isComplete ? 'status-positive' : 'caret-right-filled'}
                            onClick={() => openLab(item)}
                          >
                            {isComplete ? 'Review Completed Lab' : 'Start Lab Lesson'}
                          </Button>
                        );
                      },
                    },
                  ],
                }}
                items={filteredLabs}
                loading={loading && allLabsList.length === 0}
                loadingText="Loading lab curriculum"
                cardsPerRow={[{ cards: 1 }, { minWidth: 600, cards: 3 }]}
                empty={
                  catalogError ? (
                    <Box textAlign="center" color="inherit">
                      <b>Lab catalog unavailable</b>
                      <p>{catalogError}</p>
                      <Button onClick={loadCatalog}>Retry</Button>
                    </Box>
                  ) : (
                    <Box textAlign="center" color="inherit">
                      <b>No matching labs found</b>
                      <p>Try searching for a different keyword or level filter.</p>
                    </Box>
                  )
                }
              />
            </>
          )}
        </SpaceBetween>
      </Container>

      {/* Reset All Confirmation Modal */}
      <Modal
        visible={resetAllModalOpen}
        onDismiss={() => !resettingAll && setResetAllModalOpen(false)}
        header={`Reset All ${LAB_COUNT} Workflow Labs`}
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setResetAllModalOpen(false)} disabled={resettingAll}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleResetAllLabs} loading={resettingAll}>
                Reset All Labs
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="warning">
            This will tear down all lab-created AWS resources across all 20 services (S3 buckets, DynamoDB tables, Lambda functions, IAM roles/users, SQS queues, RDS instances, ECS clusters, EKS clusters, etc.) and clear completion progress for all {LAB_COUNT} lessons.
          </Alert>
          <p>Are you sure you want to reset every lab in the system?</p>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
};

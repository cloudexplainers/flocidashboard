import React from 'react';
import Badge from '@cloudscape-design/components/badge';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Link from '@cloudscape-design/components/link';
import ProgressBar from '@cloudscape-design/components/progress-bar';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { ConceptCard, ExamDomain, ExamTrack, LabExamTag } from '../types';
import { getCurriculumItem } from '../labCurriculum';
import { LabCompletionMap } from '../labCompletion';
import { PracticeQuiz, quizPassed, useQuizScores } from './PracticeQuiz';

/** "SAA-C03" + "2.1" -> "SAA 2.1" */
export function examTagLabel(tag: Pick<LabExamTag, 'exam' | 'task'>): string {
  return `${tag.exam.split('-')[0]} ${tag.task}`;
}

const labId = (service: string, key: string) => `${service}:${key}`;

function domainLabs(domain: ExamDomain): string[] {
  return [...new Set(domain.tasks.flatMap((task) => task.labs.map((lab) => labId(lab.service, lab.key))))];
}

export interface ExamProgress {
  /** Exam-weighted share of tagged labs completed, 0-100. Domains with no labs count as 0. */
  weighted: number;
  tasksWithLabs: number;
  taskCount: number;
}

export function examProgress(exam: ExamTrack, completed: LabCompletionMap): ExamProgress {
  let weighted = 0;
  let tasksWithLabs = 0;
  let taskCount = 0;
  for (const domain of exam.domains) {
    const labs = domainLabs(domain);
    if (labs.length > 0) {
      weighted += domain.weight * (labs.filter((id) => completed[id]).length / labs.length);
    }
    taskCount += domain.tasks.length;
    tasksWithLabs += domain.tasks.filter((task) => task.labs.length > 0).length;
  }
  return { weighted: Math.round(weighted), tasksWithLabs, taskCount };
}

interface ExamTrackViewProps {
  exam: ExamTrack;
  concepts: Record<string, ConceptCard>;
  completed: LabCompletionMap;
  onOpenLab: (service: string, key: string) => void;
}

const LabLink: React.FC<{
  service: string;
  labKey: string;
  title: string;
  primary?: boolean;
  completed: LabCompletionMap;
  onOpenLab: ExamTrackViewProps['onOpenLab'];
}> = ({ service, labKey, title, primary, completed, onOpenLab }) => {
  const curriculum = getCurriculumItem(service, labKey);
  const done = Boolean(completed[labId(service, labKey)]);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
      {done ? <StatusIndicator type="success" /> : <StatusIndicator type="pending" />}
      {curriculum && <Badge color="grey">#{curriculum.id}</Badge>}
      <Badge color="blue">{service.toUpperCase()}</Badge>
      <Button variant="inline-link" onClick={() => onOpenLab(service, labKey)}>
        {primary ? <strong>{title}</strong> : title}
      </Button>
    </div>
  );
};

const ConceptCardView: React.FC<{
  card: ConceptCard;
  completed: LabCompletionMap;
  onOpenLab: ExamTrackViewProps['onOpenLab'];
}> = ({ card, completed, onOpenLab }) => (
  <ExpandableSection headerText={`Concept: ${card.title}`} variant="footer">
    <SpaceBetween size="xs">
      <ul style={{ margin: 0, paddingLeft: '20px' }}>
        {card.points.map((point) => (
          <li key={point}>{point}</li>
        ))}
      </ul>
      {card.related_labs.length > 0 && (
        <Box variant="small">
          Related labs:{' '}
          {card.related_labs.map((lab, index) => {
            const curriculum = getCurriculumItem(lab.service, lab.key);
            return (
              <React.Fragment key={labId(lab.service, lab.key)}>
                {index > 0 && ', '}
                <Button variant="inline-link" onClick={() => onOpenLab(lab.service, lab.key)}>
                  {curriculum ? `#${curriculum.id} ${curriculum.title}` : `${lab.service}/${lab.key}`}
                </Button>
                {completed[labId(lab.service, lab.key)] ? ' ✓' : ''}
              </React.Fragment>
            );
          })}
        </Box>
      )}
      {card.planned && <Box variant="small" color="text-body-secondary">Planned lab: {card.planned}</Box>}
    </SpaceBetween>
  </ExpandableSection>
);

export const ExamTrackView: React.FC<ExamTrackViewProps> = ({ exam, concepts, completed, onOpenLab }) => {
  const progress = examProgress(exam, completed);
  const quizScores = useQuizScores();
  const quizTasks = exam.domains.flatMap((domain) => domain.tasks).filter((task) => (task.quiz?.length ?? 0) > 0);
  const quizzesPassed = quizTasks.filter((task) => quizPassed(quizScores[`${exam.code}:${task.id}`])).length;

  return (
    <SpaceBetween size="m">
      <ColumnLayout columns={2} variant="text-grid">
        <SpaceBetween size="xxs">
          <Box variant="h2">
            {exam.code} · {exam.title}
          </Box>
          <Box variant="small" color="text-body-secondary">
            Domains and task statements from the{' '}
            <Link href={exam.guide_url} external>
              official exam guide
            </Link>{' '}
            (checked {exam.checked}). Labs show the tasks they practice; concept cards cover objectives that can't be practiced in a local
            emulator.
          </Box>
        </SpaceBetween>
        <SpaceBetween size="xs">
          <ProgressBar
            value={progress.weighted}
            label="Exam-weighted lab progress"
            description={`Completed labs, weighted by each domain's share of the exam. Hands-on labs cover ${progress.tasksWithLabs} of ${progress.taskCount} task statements; the rest have concept cards.`}
            status={progress.weighted === 100 ? 'success' : 'in-progress'}
          />
          {quizTasks.length > 0 && (
            <div data-testid={`quiz-progress-${exam.code}`}>
              <Box variant="small" color="text-body-secondary">
                Practice quizzes passed: {quizzesPassed} of {quizTasks.length} task statements (70% or better).
              </Box>
            </div>
          )}
        </SpaceBetween>
      </ColumnLayout>

      {exam.domains.map((domain, index) => {
        const labs = domainLabs(domain);
        const done = labs.filter((id) => completed[id]).length;
        return (
          <ExpandableSection
            key={domain.id}
            variant="container"
            defaultExpanded={index === 0}
            headerText={`Domain ${domain.id}: ${domain.title}`}
            headerDescription={`${domain.weight}% of scored content · ${
              labs.length > 0 ? `${done} of ${labs.length} labs complete` : 'no hands-on labs yet'
            }`}
            headerActions={<Badge color={labs.length > 0 ? 'blue' : 'grey'}>{domain.weight}%</Badge>}
          >
            <SpaceBetween size="l">
              {domain.tasks.map((task) => (
                <div key={task.id} data-testid={`exam-task-${exam.code}-${task.id}`}>
                  <SpaceBetween size="xs">
                    <Box variant="h4">
                      {task.id} {task.title}
                    </Box>
                    {task.labs.length > 0 ? (
                      <SpaceBetween size="xxs">
                        {task.labs.map((lab) => (
                          <LabLink
                            key={labId(lab.service, lab.key)}
                            service={lab.service}
                            labKey={lab.key}
                            title={lab.title}
                            primary={lab.primary}
                            completed={completed}
                            onOpenLab={onOpenLab}
                          />
                        ))}
                      </SpaceBetween>
                    ) : (
                      <StatusIndicator type="warning">No hands-on lab yet</StatusIndicator>
                    )}
                    {task.concepts
                      .map((key) => concepts[key])
                      .filter(Boolean)
                      .map((card) => (
                        <ConceptCardView key={card.key} card={card} completed={completed} onOpenLab={onOpenLab} />
                      ))}
                    {task.quiz && task.quiz.length > 0 && (
                      <PracticeQuiz
                        quizId={`${exam.code}:${task.id}`}
                        questions={task.quiz}
                        best={quizScores[`${exam.code}:${task.id}`]}
                      />
                    )}
                  </SpaceBetween>
                </div>
              ))}
            </SpaceBetween>
          </ExpandableSection>
        );
      })}
      <Box variant="small" color="text-body-secondary">
        Bold labs practice this task statement as their main focus; the rest touch it along the way.
      </Box>
    </SpaceBetween>
  );
};

import React, { useEffect, useState } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Checkbox from '@cloudscape-design/components/checkbox';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import RadioGroup from '@cloudscape-design/components/radio-group';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { QuizQuestion } from '../types';

// Best quiz score per task, keyed by "EXAM:task" (for example "CLF-C02:4.3").
export type QuizScoreMap = Record<string, { correct: number; total: number }>;

const STORAGE_KEY = 'floci_quiz_scores';
const CHANGE_EVENT = 'floci:quiz-scores-changed';

export function readQuizScores(): QuizScoreMap {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    return saved ? JSON.parse(saved) : {};
  } catch {
    return {};
  }
}

function saveQuizScore(id: string, correct: number, total: number): void {
  const scores = readQuizScores();
  const best = scores[id];
  if (best && best.total === total && best.correct >= correct) return;
  const next = { ...scores, [id]: { correct, total } };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage can be unavailable (private mode); the score still shows for this visit.
  }
  window.dispatchEvent(new CustomEvent<QuizScoreMap>(CHANGE_EVENT, { detail: next }));
}

export function useQuizScores(): QuizScoreMap {
  const [scores, setScores] = useState<QuizScoreMap>(readQuizScores);
  useEffect(() => {
    const onChange = (event: Event) => setScores((event as CustomEvent<QuizScoreMap>).detail);
    window.addEventListener(CHANGE_EVENT, onChange);
    return () => window.removeEventListener(CHANGE_EVENT, onChange);
  }, []);
  return scores;
}

/** A quiz counts as passed at 70% or better, the same bar as the 700/1000 exam score. */
export const quizPassed = (score?: { correct: number; total: number }) =>
  Boolean(score && score.total > 0 && score.correct / score.total >= 0.7);

const sameAnswers = (picked: number[], answers: number[]) =>
  picked.length === answers.length && answers.every((answer) => picked.includes(answer));

const NUMBER_WORDS = ['', 'ONE', 'TWO', 'THREE', 'FOUR'];

interface PracticeQuizProps {
  quizId: string;
  questions: QuizQuestion[];
  best?: { correct: number; total: number };
}

export const PracticeQuiz: React.FC<PracticeQuizProps> = ({ quizId, questions, best }) => {
  const [picked, setPicked] = useState<number[][]>(() => questions.map(() => []));
  const [checked, setChecked] = useState(false);

  const correct = questions.filter((question, index) => sameAnswers(picked[index], question.answers)).length;
  const allAnswered = questions.every((question, index) => picked[index].length === question.answers.length);

  const choose = (questionIndex: number, option: number, multi: boolean, on: boolean) => {
    setPicked((current) =>
      current.map((choice, index) => {
        if (index !== questionIndex) return choice;
        if (!multi) return [option];
        return on ? [...choice.filter((value) => value !== option), option] : choice.filter((value) => value !== option);
      }),
    );
  };

  const check = () => {
    setChecked(true);
    saveQuizScore(quizId, correct, questions.length);
  };

  const reset = () => {
    setPicked(questions.map(() => []));
    setChecked(false);
  };

  const bestText = best ? ` · best ${best.correct}/${best.total}` : '';

  return (
    <ExpandableSection headerText={`Practice quiz: ${questions.length} questions${bestText}`} variant="footer">
      <div data-testid={`quiz-${quizId}`}>
        <SpaceBetween size="m">
          {questions.map((question, questionIndex) => {
            const multi = question.answers.length > 1;
            const choice = picked[questionIndex];
            const right = sameAnswers(choice, question.answers);
            const items = question.options.map((option, optionIndex) => ({ value: String(optionIndex), label: option }));
            return (
              <div key={question.question} data-testid={`quiz-question-${questionIndex + 1}`}>
                <SpaceBetween size="xxs">
                  <Box variant="strong">
                    {questionIndex + 1}. {question.question}
                  </Box>
                  {multi ? (
                    <SpaceBetween size="xxs">
                      {question.options.map((option, optionIndex) => (
                        <Checkbox
                          key={option}
                          checked={choice.includes(optionIndex)}
                          disabled={checked || (!choice.includes(optionIndex) && choice.length >= question.answers.length)}
                          onChange={({ detail }) => choose(questionIndex, optionIndex, true, detail.checked)}
                        >
                          {option}
                        </Checkbox>
                      ))}
                      {!question.question.includes('(Choose') && (
                        <Box variant="small">Choose {NUMBER_WORDS[question.answers.length] || question.answers.length}.</Box>
                      )}
                    </SpaceBetween>
                  ) : (
                    <RadioGroup
                      value={choice.length ? String(choice[0]) : null}
                      items={items.map((item) => ({ ...item, disabled: checked }))}
                      onChange={({ detail }) => choose(questionIndex, Number(detail.value), false, true)}
                    />
                  )}
                  {checked && (
                    <SpaceBetween size="xxs">
                      <StatusIndicator type={right ? 'success' : 'error'}>
                        {right ? 'Correct' : `Answer: ${question.answers.map((answer) => question.options[answer]).join('; ')}`}
                      </StatusIndicator>
                      <Box variant="small" color="text-body-secondary">
                        {question.explanation}
                      </Box>
                    </SpaceBetween>
                  )}
                </SpaceBetween>
              </div>
            );
          })}
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            {checked ? (
              <>
                <StatusIndicator type={quizPassed({ correct, total: questions.length }) ? 'success' : 'warning'}>
                  {correct} of {questions.length} correct
                </StatusIndicator>
                <Button onClick={reset}>Try again</Button>
              </>
            ) : (
              <Button variant="primary" disabled={!allAnswered} onClick={check}>
                Check answers
              </Button>
            )}
          </SpaceBetween>
        </SpaceBetween>
      </div>
    </ExpandableSection>
  );
};

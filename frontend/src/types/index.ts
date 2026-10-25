export interface ActionField {
  name: string;
  label: string;
  required?: boolean;
  field_type?: string;
  help_text?: string;
  /** Allowed values (AWS enum values), shown as a select. */
  options?: string[];
  /** Initial value; sent unless changed. */
  default?: string | number | boolean | null;
}

export interface ServiceAction {
  name: string;
  label: string;
  method: string;
  path: string;
  kind: 'read' | 'create' | 'update' | 'delete' | 'execute';
  safety?: 'safe' | 'mutating' | 'destructive';
  description?: string;
  fields?: ActionField[];
  confirm?: string;
  success_message?: string;
}

export interface ServiceDefinition {
  key: string;
  title: string;
  eyebrow: string;
  category: string;
  maturity: 'missing' | 'inventory_only' | 'read_only_inspector' | 'interactive_workbench' | 'tutorial_ready';
  api_path: string;
  docs_url?: string;
  tags?: string[];
  actions?: ServiceAction[];
  count?: number;
  running?: boolean;
  status?: string;
}

export interface LabStepSnippet {
  cli: string;
  boto3: string;
  terraform: string;
}

export interface LabStep {
  key: string;
  title: string;
  command: string;
  explanation: string;
  artifact?: string;
  artifact_label?: string;
  secondary_artifact?: string;
  secondary_artifact_label?: string;
  snippets?: LabStepSnippet;
  status?: {
    verified?: boolean;
    verification?: {
      message?: string;
      verified?: boolean;
    };
  };
}

export interface LabExamTag {
  exam: string;
  task: string;
  primary: boolean;
}

export interface LabDefinition {
  key: string;
  service: string;
  title: string;
  description: string;
  steps: LabStep[];
  step_count?: number;
  complete?: boolean;
  exams?: LabExamTag[];
}

export interface ExamTaskLab {
  service: string;
  key: string;
  title: string;
  primary: boolean;
}

export interface QuizQuestion {
  question: string;
  options: string[];
  /** Indexes of the correct options; more than one makes it a "choose N" question. */
  answers: number[];
  explanation: string;
}

export interface ExamTask {
  id: string;
  title: string;
  labs: ExamTaskLab[];
  concepts: string[];
  quiz?: QuizQuestion[];
}

export interface ExamDomain {
  id: string;
  title: string;
  weight: number;
  tasks: ExamTask[];
}

export interface ExamTrack {
  code: string;
  title: string;
  short_title: string;
  level: string;
  guide_url: string;
  checked: string;
  passing_score: number;
  domains: ExamDomain[];
}

export interface ConceptCard {
  key: string;
  title: string;
  points: string[];
  exams: Array<{ exam: string; task: string }>;
  related_labs: Array<{ service: string; key: string }>;
  planned: string | null;
}

export interface ExamTracksResponse {
  exams: ExamTrack[];
  concepts: ConceptCard[];
}

export interface IdentityInfo {
  account_id: string;
  user_id: string;
  arn: string;
  role: string;
  region: string;
  endpoint: string;
  resolved?: boolean;
  error?: string;
  expires_at?: string | null;
}

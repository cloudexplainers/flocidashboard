import { parseEksRoute, eksClusterPath } from './pages/eksResources';
import { parseEcsRoute, clusterPath, servicePath } from './pages/ecsResources';
import { useSession } from './components/SessionContext';
import React, { useState, useEffect } from 'react';
import { TopNav } from './components/TopNav';
import { ConsoleLayout } from './components/ConsoleLayout';
import { ConsoleHome } from './pages/ConsoleHome';
import { S3Console } from './pages/S3Console';
import { EC2Console } from './pages/EC2Console';
import { IAMConsole } from './pages/IAMConsole';
import { DynamoDBConsole } from './pages/DynamoDBConsole';
import { LambdaConsole } from './pages/LambdaConsole';
import { SQSConsole } from './pages/SQSConsole';
import { SNSConsole } from './pages/SNSConsole';
import { RDSConsole } from './pages/RDSConsole';
import { KMSConsole } from './pages/KMSConsole';
import { SecretsManagerConsole } from './pages/SecretsManagerConsole';
import { CloudFormationConsole } from './pages/CloudFormationConsole';
import { Route53Console } from './pages/Route53Console';
import { EventBridgeConsole } from './pages/EventBridgeConsole';
import { CloudWatchConsole } from './pages/CloudWatchConsole';
import { StepFunctionsConsole } from './pages/StepFunctionsConsole';
import { CognitoConsole } from './pages/CognitoConsole';
import { ApiGatewayConsole } from './pages/ApiGatewayConsole';
import { SSMConsole } from './pages/SSMConsole';
import { ECSConsole } from './pages/ECSConsole';
import { EKSConsole } from './pages/EKSConsole';
import { ECRConsole } from './pages/ECRConsole';
import { CloudFrontConsole } from './pages/CloudFrontConsole';
import { ELBv2Console } from './pages/ELBv2Console';
import { AthenaConsole } from './pages/AthenaConsole';
import { AppSyncConsole } from './pages/AppSyncConsole';
import { SESConsole } from './pages/SESConsole';
import { LabsConsole } from './pages/LabsConsole';
import { InspectorConsole } from './pages/InspectorConsole';
import { SettingsConsole } from './pages/SettingsConsole';
import { EnvironmentConsole } from './pages/EnvironmentConsole';
import { ActivityConsole } from './pages/ActivityConsole';
import { CliConsole } from './pages/CliConsole';
import { ResourceGraphConsole } from './pages/ResourceGraphConsole';
import { GenericServiceWorkbench } from './pages/GenericServiceWorkbench';
import { AwsServiceIcon } from './components/AwsServiceIcons';
import { FavoritesBar } from './components/FavoritesBar';
import { fetchServices } from './api/client';
import { LAB_CURRICULUM } from './labCurriculum';
import { ServiceDefinition } from './types';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Button from '@cloudscape-design/components/button';

function parseUrlPath(): { mainView: string; subTab?: string; fullView: string } {
  const path = window.location.pathname.replace(/^\/app(?:\/|$)/, '').replace(/^\//, '').replace(/\/$/, '');
  const search = window.location.search;
  const fullView = path ? (search ? `${path}${search}` : path) : (search ? `home${search}` : 'home');
  if (!path || path === 'app' || path === 'home') return { mainView: 'home', fullView };
  const pathWithoutQuery = path.split('?')[0];
  const parts = pathWithoutQuery.split('/');
  return { mainView: parts[0] || 'home', subTab: parts[1], fullView };
}

export const App: React.FC = () => {
  const initial = parseUrlPath();
  const { identity } = useSession();
  const [services, setServices] = useState<ServiceDefinition[]>([]);
  const [currentView, setCurrentView] = useState<string>(initial.mainView);
  const [activeSubView, setActiveSubView] = useState<string>(initial.fullView);
  const [activeTabOverride, setActiveTabOverride] = useState<Record<string, string>>(() => {
    if (initial.subTab) {
      return { [initial.mainView]: initial.subTab };
    }
    return {};
  });

  useEffect(() => {
    fetchServices().then(setServices);

    const handlePopState = () => {
      const parsed = parseUrlPath();
      setCurrentView(parsed.mainView);
      setActiveSubView(parsed.fullView);
      if (parsed.subTab) {
        setActiveTabOverride((prev) => ({ ...prev, [parsed.mainView]: parsed.subTab! }));
      }
      window.dispatchEvent(new CustomEvent('floci_navigate', { detail: { view: parsed.fullView, mainView: parsed.mainView, subTab: parsed.subTab } }));
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  useEffect(() => {
    if (activeSubView.split('/')[0] === currentView && activeSubView.includes('/')) {
      const target = `/${activeSubView}`;
      if (window.location.pathname + window.location.search !== target) window.history.pushState(null, '', target);
    }
  }, [activeSubView, currentView]);

  const navigateTo = (view: string) => {
    if (view === 'home' || !view) {
      setCurrentView('home');
      setActiveSubView('home');
      if (window.location.pathname !== '/') {
        window.history.pushState(null, '', '/');
      }
      window.dispatchEvent(new CustomEvent('floci_navigate', { detail: { view: 'home', mainView: 'home' } }));
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    const pathPart = view.split('?')[0];
    const queryPart = view.includes('?') ? view.split('?')[1] : '';
    const parts = pathPart.split('/');
    const main = parts[0] || 'home';
    const sub = parts[1];

    try {
      if (services.some(service => service.key === main)) {
        const previous: string[] = JSON.parse(localStorage.getItem('floci_recent_services') || '[]');
        localStorage.setItem('floci_recent_services', JSON.stringify([main, ...previous.filter(key => key !== main)].slice(0, 8)));
      }
    } catch { /* Storage may be unavailable. */ }
    setCurrentView(main);
    setActiveSubView(view);
    if (sub) {
      setActiveTabOverride((prev) => ({ ...prev, [main]: sub }));
    }

    const targetPath = queryPart ? `/${pathPart}?${queryPart}` : `/${pathPart}`;
    if (window.location.pathname + window.location.search !== targetPath) {
      window.history.pushState(null, '', targetPath);
    }
    window.dispatchEvent(new CustomEvent('floci_navigate', { detail: { view, mainView: main, subTab: sub, query: queryPart } }));
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const getBreadcrumbs = () => {
    if (currentView === 'home') {
      return [{ text: 'AWS Management Console', href: '#' }];
    }
    if (currentView === 'labs') {
      return [
        { text: 'AWS Management Console', href: 'home' },
        { text: 'Workflow Labs', href: 'labs' },
      ];
    }
    if (currentView === 'inspector') {
      return [
        { text: 'AWS Management Console', href: 'home' },
        { text: 'Developer Inspector', href: 'inspector' },
      ];
    }
    if (currentView === 'topology') {
      return [
        { text: 'AWS Management Console', href: 'home' },
        { text: 'Architecture Topology Graph', href: 'topology' },
      ];
    }
    if (currentView === 'settings') {
      return [
        { text: 'AWS Management Console', href: 'home' },
        { text: 'Dashboard Settings', href: 'settings' },
      ];
    }
    if (currentView === 'environment') {
      return [
        { text: 'AWS Management Console', href: 'home' },
        { text: 'Session Identity & STS', href: 'environment' },
      ];
    }
    if (currentView === 'activity') {
      return [
        { text: 'AWS Management Console', href: 'home' },
        { text: 'Activity & Audit Log', href: 'activity' },
      ];
    }
    if (currentView === 'eks') {
      const route = parseEksRoute(activeSubView);
      if (route.cluster) return [
        { text: 'AWS Management Console', href: 'home' }, { text: 'Amazon EKS', href: 'eks/clusters' },
        { text: route.cluster, href: eksClusterPath(route.cluster) },
        ...(route.nodegroup ? [{ text: route.nodegroup, href: activeSubView }] : []),
      ];
    }
    if (currentView === 'ecs') {
      const route = parseEcsRoute(activeSubView);
      if (route.cluster) return [
        { text: 'AWS Management Console', href: 'home' }, { text: 'Amazon ECS', href: 'ecs/clusters' },
        { text: route.cluster, href: clusterPath(route.cluster) },
        ...(route.service ? [{ text: route.service, href: servicePath(route.cluster, route.service) }] : []),
        ...(route.task ? [{ text: route.task, href: activeSubView }] : []),
      ];
    }
    if (currentView === 'route53' || currentView === 'cognito') {
      const query = new URLSearchParams(activeSubView.split('?')[1] || '');
      const id = query.get(currentView === 'route53' ? 'zone' : 'pool');
      if (id) return [
        { text: 'AWS Management Console', href: 'home' },
        { text: currentView === 'route53' ? 'Amazon Route 53' : 'Amazon Cognito', href: currentView },
        { text: id, href: activeSubView },
        ...(query.get('client') ? [{ text: query.get('client')!, href: activeSubView }] : []),
      ];
    }
    const svc = services.find((s) => s.key === currentView);
    const sub = activeSubView.split('/')[1]?.split('?')[0];
    return [
      { text: 'AWS Management Console', href: 'home' },
      { text: svc ? svc.title : currentView.toUpperCase(), href: currentView },
      ...(sub ? [{ text: sub.replace(/-/g, ' '), href: activeSubView }] : []),
    ];
  };

  const getSideNavHeader = () => {
    if (currentView === 'home') {
      return { href: 'home', text: 'AWS Management Console' };
    }
    const svc = services.find((s) => s.key === currentView);
    if (svc) {
      return { href: currentView, text: svc.title };
    }
    const specialHeaders: Record<string, string> = {
      labs: 'AWS Workflow Labs (66)',
      inspector: 'Developer Inspector',
      topology: 'Architecture Topology Graph',
      settings: 'Dashboard Settings',
      environment: 'Session Identity & STS',
      activity: 'Activity Audit Log',
      console: 'AWS CLI Terminal',
    };
    return { href: currentView, text: specialHeaders[currentView] || currentView.toUpperCase() };
  };

  const getSideNavItems = () => {
    if (currentView === 'home') {
      return [
        { type: 'link' as const, text: 'Console Home', href: 'home', icon: <AwsServiceIcon service="aws" size={18} /> },
        { type: 'link' as const, text: 'Workflow Labs (66)', href: 'labs', icon: <AwsServiceIcon service="labs" size={18} /> },
        { type: 'link' as const, text: 'Local Inspector Inbox', href: 'inspector', icon: <AwsServiceIcon service="inspector" size={18} /> },
        { type: 'link' as const, text: 'Architecture Topology Graph', href: 'topology', icon: <AwsServiceIcon service="topology" size={18} /> },
        { type: 'divider' as const },
        {
          type: 'section' as const,
          text: 'Developer Tools & Management',
          items: [
            { type: 'link' as const, text: 'AWS CLI Terminal', href: 'console', icon: <AwsServiceIcon service="aws" size={18} /> },
            { type: 'link' as const, text: 'Activity Audit Log', href: 'activity', icon: <AwsServiceIcon service="cloudwatch" size={18} /> },
            { type: 'link' as const, text: 'Session Identity & STS', href: 'environment', icon: <AwsServiceIcon service="iam" size={18} /> },
            { type: 'link' as const, text: 'Dashboard Settings', href: 'settings', icon: <AwsServiceIcon service="ssm" size={18} /> },
          ],
        },
        { type: 'divider' as const },
        {
          type: 'section' as const,
          text: 'Core AWS Services',
          items: [
            { type: 'link' as const, text: 'Amazon S3', href: 's3', icon: <AwsServiceIcon service="s3" size={18} /> },
            { type: 'link' as const, text: 'Amazon EC2', href: 'ec2', icon: <AwsServiceIcon service="ec2" size={18} /> },
            { type: 'link' as const, text: 'AWS IAM', href: 'iam', icon: <AwsServiceIcon service="iam" size={18} /> },
            { type: 'link' as const, text: 'Amazon DynamoDB', href: 'dynamodb', icon: <AwsServiceIcon service="dynamodb" size={18} /> },
            { type: 'link' as const, text: 'AWS Lambda', href: 'lambda', icon: <AwsServiceIcon service="lambda" size={18} /> },
            { type: 'link' as const, text: 'Amazon SQS', href: 'sqs', icon: <AwsServiceIcon service="sqs" size={18} /> },
            { type: 'link' as const, text: 'Amazon SNS', href: 'sns', icon: <AwsServiceIcon service="sns" size={18} /> },
            { type: 'link' as const, text: 'Amazon RDS', href: 'rds', icon: <AwsServiceIcon service="rds" size={18} /> },
            { type: 'link' as const, text: 'AWS KMS', href: 'kms', icon: <AwsServiceIcon service="kms" size={18} /> },
            { type: 'link' as const, text: 'AWS Secrets Manager', href: 'secretsmanager', icon: <AwsServiceIcon service="secretsmanager" size={18} /> },
            { type: 'link' as const, text: 'AWS CloudFormation', href: 'cloudformation', icon: <AwsServiceIcon service="cloudformation" size={18} /> },
            { type: 'link' as const, text: 'Amazon Route 53', href: 'route53', icon: <AwsServiceIcon service="route53" size={18} /> },
            { type: 'link' as const, text: 'Amazon EventBridge', href: 'eventbridge', icon: <AwsServiceIcon service="eventbridge" size={18} /> },
            { type: 'link' as const, text: 'Amazon CloudWatch', href: 'cloudwatch', icon: <AwsServiceIcon service="cloudwatch" size={18} /> },
            { type: 'link' as const, text: 'AWS Step Functions', href: 'stepfunctions', icon: <AwsServiceIcon service="stepfunctions" size={18} /> },
            { type: 'link' as const, text: 'Amazon Cognito', href: 'cognito', icon: <AwsServiceIcon service="cognito" size={18} /> },
            { type: 'link' as const, text: 'Amazon API Gateway', href: 'apigateway', icon: <AwsServiceIcon service="apigateway" size={18} /> },
            { type: 'link' as const, text: 'AWS Systems Manager (SSM)', href: 'ssm', icon: <AwsServiceIcon service="ssm" size={18} /> },
            { type: 'link' as const, text: 'Amazon ECS', href: 'ecs', icon: <AwsServiceIcon service="ecs" size={18} /> },
            { type: 'link' as const, text: 'Amazon ECR', href: 'ecr', icon: <AwsServiceIcon service="ecr" size={18} /> },
            { type: 'link' as const, text: 'Amazon CloudFront', href: 'cloudfront', icon: <AwsServiceIcon service="cloudfront" size={18} /> },
            { type: 'link' as const, text: 'Elastic Load Balancing (ELBv2)', href: 'elasticloadbalancing', icon: <AwsServiceIcon service="elasticloadbalancing" size={18} /> },
            { type: 'link' as const, text: 'Amazon Athena', href: 'athena', icon: <AwsServiceIcon service="athena" size={18} /> },
            { type: 'link' as const, text: 'AWS AppSync', href: 'appsync', icon: <AwsServiceIcon service="appsync" size={18} /> },
            { type: 'link' as const, text: 'Amazon SES', href: 'ses', icon: <AwsServiceIcon service="ses" size={18} /> },
          ],
        },
      ];
    }

    // Context-specific active navigation per service
    switch (currentView) {
      case 's3':
        return [
          { type: 'link' as const, text: 'Buckets & Objects', href: 's3/objects', icon: <AwsServiceIcon service="s3" size={18} /> },
          { type: 'link' as const, text: 'Properties & Versioning', href: 's3/properties' },
          { type: 'link' as const, text: 'Permissions & Policy', href: 's3/permissions' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'ec2':
        return [
          {
            type: 'section' as const,
            text: 'Instances',
            items: [
              { type: 'link' as const, text: 'Instances', href: 'ec2/instances', icon: <AwsServiceIcon service="ec2" size={18} /> },
            ],
          },
          {
            type: 'section' as const,
            text: 'Network & Security',
            items: [
              { type: 'link' as const, text: 'Security Groups', href: 'ec2/security_groups' },
              { type: 'link' as const, text: 'Virtual Private Clouds (VPC)', href: 'ec2/vpcs' },
              { type: 'link' as const, text: 'Subnets', href: 'ec2/subnets' },
              { type: 'link' as const, text: 'Route tables', href: 'ec2/routes' },
              { type: 'link' as const, text: 'Gateways and endpoints', href: 'ec2/gateways' },
            ],
          },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'iam':
        return [
          {
            type: 'section' as const,
            text: 'Access Management',
            items: [
              { type: 'link' as const, text: 'Roles', href: 'iam/roles', icon: <AwsServiceIcon service="iam" size={18} /> },
              { type: 'link' as const, text: 'Users', href: 'iam/users' },
              { type: 'link' as const, text: 'User Groups', href: 'iam/groups' },
              { type: 'link' as const, text: 'Policies', href: 'iam/policies' },
            ],
          },
          {
            type: 'section' as const,
            text: 'Access Analysis',
            items: [
              { type: 'link' as const, text: 'Policy Simulator', href: 'iam/simulator' },
              { type: 'link' as const, text: 'Session Identity & STS', href: 'environment' },
            ],
          },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'dynamodb':
        return [
          { type: 'link' as const, text: 'Explore Items (CRUD)', href: 'dynamodb/items', icon: <AwsServiceIcon service="dynamodb" size={18} /> },
          { type: 'link' as const, text: 'PartiQL SQL Query Editor', href: 'dynamodb/partiql' },
          { type: 'link' as const, text: 'Tables & Schema', href: 'dynamodb/overview' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'lambda':
        return [
          { type: 'link' as const, text: 'Functions & Code Preview', href: 'lambda/code', icon: <AwsServiceIcon service="lambda" size={18} /> },
          { type: 'link' as const, text: 'Function URLs (Public)', href: 'lambda/urls' },
          { type: 'link' as const, text: 'Triggers (Event Sources)', href: 'lambda/triggers' },
          { type: 'link' as const, text: 'Versions & Snapshots', href: 'lambda/versions' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'sqs':
        return [
          { type: 'link' as const, text: 'Queues & Message Workbench', href: 'sqs', icon: <AwsServiceIcon service="sqs" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'sns':
        return [
          { type: 'link' as const, text: 'Topics & Subscriptions', href: 'sns', icon: <AwsServiceIcon service="sns" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'rds':
        return [
          { type: 'link' as const, text: 'Databases, Clusters & Parameter Groups', href: 'rds', icon: <AwsServiceIcon service="rds" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'kms':
        return [
          { type: 'link' as const, text: 'Customer Managed Keys', href: 'kms/keys', icon: <AwsServiceIcon service="kms" size={18} /> },
          { type: 'link' as const, text: 'Cryptographic Workbench', href: 'kms/workbench' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'secretsmanager':
        return [
          { type: 'link' as const, text: 'Secrets & Decryption Vault', href: 'secretsmanager', icon: <AwsServiceIcon service="secretsmanager" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'cloudformation':
        return [
          { type: 'link' as const, text: 'Stacks, Events & Change Sets', href: 'cloudformation', icon: <AwsServiceIcon service="cloudformation" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'route53':
        return [
          { type: 'link' as const, text: 'Hosted Zones & DNS Records', href: 'route53', icon: <AwsServiceIcon service="route53" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'eventbridge':
        return [
          { type: 'link' as const, text: 'Rules and routing', href: 'eventbridge/rules', icon: <AwsServiceIcon service="eventbridge" size={18} /> },
          { type: 'link' as const, text: 'Event buses', href: 'eventbridge/buses' },
          { type: 'link' as const, text: 'Pattern tester', href: 'eventbridge/tester' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'cloudwatch':
        return [
          { type: 'link' as const, text: 'Metric Alarms', href: 'cloudwatch/alarms', icon: <AwsServiceIcon service="cloudwatch" size={18} /> },
          { type: 'link' as const, text: 'Metrics Explorer & Ingestion', href: 'cloudwatch/metrics' },
          { type: 'link' as const, text: 'Log Streams & Retention', href: 'cloudwatch/logs' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'stepfunctions':
        return [
          { type: 'link' as const, text: 'State Machines & Executions', href: 'stepfunctions/machines', icon: <AwsServiceIcon service="stepfunctions" size={18} /> },
          { type: 'link' as const, text: 'Activities (Worker Pollers)', href: 'stepfunctions/activities' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'cognito':
        return [
          { type: 'link' as const, text: 'User Pools & Groups', href: `cognito/users${activeSubView.includes('?') ? '?' + activeSubView.split('?')[1] : ''}`, icon: <AwsServiceIcon service="cognito" size={18} /> },
          { type: 'link' as const, text: 'Identity Pools (Federated)', href: `cognito/identities${activeSubView.includes('?') ? '?' + activeSubView.split('?')[1] : ''}` },
          { type: 'link' as const, text: 'Auth & JWT Decoder Sandbox', href: `cognito/auth${activeSubView.includes('?') ? '?' + activeSubView.split('?')[1] : ''}` },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'apigateway':
        return [
          { type: 'link' as const, text: 'Resources & Methods', href: 'apigateway/resources', icon: <AwsServiceIcon service="apigateway" size={18} /> },
          { type: 'link' as const, text: 'Stages & Deployments', href: 'apigateway/stages' },
          { type: 'link' as const, text: 'Authorizers', href: 'apigateway/authorizers' },
          { type: 'link' as const, text: 'Test Request Runner', href: 'apigateway/test' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'ssm':
        return [
          { type: 'link' as const, text: 'Parameters & Secrets', href: 'ssm/parameters', icon: <AwsServiceIcon service="ssm" size={18} /> },
          { type: 'link' as const, text: 'SSM Documents & Runbooks', href: 'ssm/documents' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'ecs':
        return [
          { type: 'link' as const, text: 'Clusters', href: 'ecs/clusters', icon: <AwsServiceIcon service="ecs" size={18} /> },
          { type: 'link' as const, text: 'Services', href: 'ecs/services' },
          { type: 'link' as const, text: 'Tasks', href: 'ecs/tasks' },
          { type: 'link' as const, text: 'Task Definitions', href: 'ecs/definitions' },
          { type: 'link' as const, text: 'Container Instances', href: 'ecs/instances' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'ecr':
        return [
          { type: 'link' as const, text: 'Repositories', href: 'ecr/repositories', icon: <AwsServiceIcon service="ecr" size={18} /> },
          { type: 'link' as const, text: 'Images Explorer', href: 'ecr/images' },
          { type: 'link' as const, text: 'Lifecycle Policy & Mutability', href: 'ecr/lifecycle' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'cloudfront':
        return [
          { type: 'link' as const, text: 'Distributions', href: 'cloudfront/distributions', icon: <AwsServiceIcon service="cloudfront" size={18} /> },
          { type: 'link' as const, text: 'Invalidations', href: 'cloudfront/invalidations' },
          { type: 'link' as const, text: 'Origins & Behaviors', href: 'cloudfront/origins' },
          { type: 'link' as const, text: 'Cache Policies', href: 'cloudfront/cache-policies' },
          { type: 'link' as const, text: 'Functions', href: 'cloudfront/functions' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'elasticloadbalancing':
      case 'elbv2':
      case 'elb':
        return [
          { type: 'link' as const, text: 'Load Balancers', href: 'elasticloadbalancing/loadbalancers', icon: <AwsServiceIcon service="elasticloadbalancing" size={18} /> },
          { type: 'link' as const, text: 'Target Groups', href: 'elasticloadbalancing/targetgroups' },
          { type: 'link' as const, text: 'Registered Targets', href: 'elasticloadbalancing/targets' },
          { type: 'link' as const, text: 'Listeners', href: 'elasticloadbalancing/listeners' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'athena':
        return [
          { type: 'link' as const, text: 'Query Editor', href: 'athena/editor', icon: <AwsServiceIcon service="athena" size={18} /> },
          { type: 'link' as const, text: 'Recent Queries', href: 'athena/history' },
          { type: 'link' as const, text: 'Workgroups', href: 'athena/workgroups' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'appsync':
        return [
          { type: 'link' as const, text: 'APIs', href: 'appsync/apis', icon: <AwsServiceIcon service="appsync" size={18} /> },
          { type: 'link' as const, text: 'Schema Editor', href: 'appsync/schema' },
          { type: 'link' as const, text: 'Data Sources', href: 'appsync/datasources' },
          { type: 'link' as const, text: 'Resolvers', href: 'appsync/resolvers' },
          { type: 'link' as const, text: 'API Keys', href: 'appsync/apikeys' },
          { type: 'link' as const, text: 'GraphQL Query Sandbox', href: 'appsync/sandbox' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'ses':
        return [
          { type: 'link' as const, text: 'Verified Identities', href: 'ses/identities', icon: <AwsServiceIcon service="ses" size={18} /> },
          { type: 'link' as const, text: 'Send Email Workbench', href: 'ses/send' },
          { type: 'link' as const, text: 'Email Templates', href: 'ses/templates' },
          { type: 'link' as const, text: 'Configuration Sets', href: 'ses/configsets' },
          { type: 'link' as const, text: 'Test Mailbox', href: 'ses/mailbox' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'topology':
        return [
          { type: 'link' as const, text: 'Application Spines & Canvas', href: 'topology', icon: <AwsServiceIcon service="topology" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'labs':
        return [
          {
            type: 'link' as const,
            text: 'All Labs Overview (66)',
            href: 'labs',
            icon: <AwsServiceIcon service="labs" size={18} />,
          },
          { type: 'divider' as const },
          ...LAB_CURRICULUM.map((level) => ({
            type: 'section' as const,
            text: `${level.title} (${level.labs.length})`,
            items: level.labs.map((lab) => ({
              type: 'link' as const,
              text: `#${lab.id}. [${lab.service.toUpperCase()}] ${lab.title}`,
              href: `labs?service=${lab.service}&lab=${lab.key}`,
            })),
          })),
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'inspector':
        return [
          { type: 'link' as const, text: 'Developer Inspector Inbox', href: 'inspector', icon: <AwsServiceIcon service="inspector" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'settings':
      case 'environment':
      case 'activity':
      case 'console':
        return [
          { type: 'link' as const, text: 'Dashboard Settings', href: 'settings', icon: <AwsServiceIcon service="ssm" size={18} /> },
          { type: 'link' as const, text: 'Session Identity & STS', href: 'environment', icon: <AwsServiceIcon service="iam" size={18} /> },
          { type: 'link' as const, text: 'Activity Audit Log', href: 'activity', icon: <AwsServiceIcon service="cloudwatch" size={18} /> },
          { type: 'link' as const, text: 'AWS CLI Terminal', href: 'console', icon: <AwsServiceIcon service="aws" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      default:
        return [
          { type: 'link' as const, text: 'Actions & Operations', href: currentView, icon: <AwsServiceIcon service={currentView} size={18} /> },
          { type: 'link' as const, text: 'Service Inventory & Health', href: currentView },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
    }
  };

  const renderContent = () => {
    switch (currentView) {
      case 'home':
        return (
          <ConsoleHome
            services={services}
            onSelectService={(key) => navigateTo(key)}
            onNavigateLabs={() => navigateTo('labs')}
            onNavigateInspector={() => navigateTo('inspector')}
          />
        );
      case 's3':
        return (
          <S3Console
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['s3']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, s3: tab }));
              setActiveSubView(`s3/${tab}`);
            }}
          />
        );
      case 'ec2':
        return (
          <EC2Console
            activeTab={activeTabOverride['ec2']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, ec2: tab }));
              setActiveSubView(`ec2/${tab}`);
            }}
          />
        );
      case 'iam':
        return (
          <IAMConsole
            activeTab={activeTabOverride['iam']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, iam: tab }));
              setActiveSubView(`iam/${tab}`);
            }}
          />
        );
      case 'dynamodb':
        return (
          <DynamoDBConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['dynamodb']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, dynamodb: tab }));
              setActiveSubView(`dynamodb/${tab}`);
            }}
          />
        );
      case 'lambda':
        return (
          <LambdaConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['lambda']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, lambda: tab }));
              setActiveSubView(`lambda/${tab}`);
            }}
          />
        );
      case 'sqs':
        return <SQSConsole resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'sns':
        return <SNSConsole resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'rds':
        return <RDSConsole />;
      case 'kms':
        return (
          <KMSConsole
            activeTab={activeTabOverride['kms']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, kms: tab }));
              setActiveSubView(`kms/${tab}`);
            }}
          />
        );
      case 'secretsmanager':
        return <SecretsManagerConsole />;
      case 'cloudformation':
        return <CloudFormationConsole />;
      case 'route53':
        return <Route53Console resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'eventbridge':
        return <EventBridgeConsole resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'cloudwatch':
        return (
          <CloudWatchConsole
            activeTab={activeTabOverride['cloudwatch']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, cloudwatch: tab }));
              setActiveSubView(`cloudwatch/${tab}`);
            }}
          />
        );
      case 'stepfunctions':
        return (
          <StepFunctionsConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['stepfunctions']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, stepfunctions: tab }));
              setActiveSubView(`stepfunctions/${tab}`);
            }}
          />
        );
      case 'cognito':
        return (
          <CognitoConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['cognito']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, cognito: tab }));
              setActiveSubView(`cognito/${tab}`);
            }}
          />
        );
      case 'apigateway':
        return (
          <ApiGatewayConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['apigateway']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, apigateway: tab }));
              setActiveSubView(`apigateway/${tab}`);
            }}
          />
        );
      case 'ssm':
        return (
          <SSMConsole
            activeTab={activeTabOverride['ssm']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, ssm: tab }));
              setActiveSubView(`ssm/${tab}`);
            }}
          />
        );
      case 'ecs':
        return (
          <ECSConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['ecs']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, ecs: tab }));
              setActiveSubView(`ecs/${tab}`);
            }}
          />
        );
      case 'eks':
        return (
          <EKSConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['eks']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, eks: tab }));
              setActiveSubView(`eks/${tab}`);
            }}
          />
        );
      case 'ecr':
        return (
          <ECRConsole
            activeTab={activeTabOverride['ecr']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, ecr: tab }));
              setActiveSubView(`ecr/${tab}`);
            }}
          />
        );
      case 'cloudfront':
        return (
          <CloudFrontConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['cloudfront']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, cloudfront: tab }));
              setActiveSubView(`cloudfront/${tab}`);
            }}
          />
        );
      case 'elasticloadbalancing':
      case 'elbv2':
      case 'elb':
        return (
          <ELBv2Console
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['elasticloadbalancing'] || activeTabOverride['elbv2']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, elasticloadbalancing: tab, elbv2: tab }));
              setActiveSubView(`elasticloadbalancing/${tab}`);
            }}
          />
        );
      case 'athena':
        return (
          <AthenaConsole
            activeTab={activeTabOverride['athena']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, athena: tab }));
              setActiveSubView(`athena/${tab}`);
            }}
          />
        );
      case 'appsync':
        return (
          <AppSyncConsole
            activeTab={activeTabOverride['appsync']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, appsync: tab }));
              setActiveSubView(`appsync/${tab}`);
            }}
          />
        );
      case 'ses':
        return (
          <SESConsole
            activeTab={activeTabOverride['ses']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, ses: tab }));
              setActiveSubView(`ses/${tab}`);
            }}
          />
        );
      case 'labs':
        return <LabsConsole />;
      case 'inspector':
        return <InspectorConsole />;
      case 'settings':
        return <SettingsConsole />;
      case 'environment':
        return <EnvironmentConsole />;
      case 'activity':
        return <ActivityConsole onNavigateService={(s) => navigateTo(s)} />;
      case 'console':
        return <CliConsole />;
      case 'topology':
        return <ResourceGraphConsole onNavigateService={(s) => navigateTo(s)} />;
      default:
        const svc = services.find((s) => s.key === currentView);
        if (svc) {
          return (
            <GenericServiceWorkbench
              key={svc.key}
              service={svc}
              onNavigateLabs={() => navigateTo('labs')}
            />
          );
        }
        return (
          <Container
            header={
              <Header variant="h1" description="Service Not Found">
                {currentView.toUpperCase()}
              </Header>
            }
          >
            <SpaceBetween size="m">
              <p>The requested service was not found or is not registered.</p>
              <Button onClick={() => navigateTo('home')}>
                ← Return to Console Home
              </Button>
            </SpaceBetween>
          </Container>
        );
    }
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <TopNav
        identity={identity}
        services={services}
        onSelectService={(key) => navigateTo(key)}
        onNavigateHome={() => navigateTo('home')}
        onNavigateLabs={() => navigateTo('labs')}
      />
      <FavoritesBar
        activeService={currentView}
        onSelectService={(key) => navigateTo(key)}
        allServices={services}
      />
      <ConsoleLayout
        breadcrumbs={getBreadcrumbs()}
        sideNavHeader={getSideNavHeader()}
        sideNavItems={getSideNavItems()}
        activeSideNavHref={activeSubView}
        onSideNavFollow={(href) => navigateTo(href)}
      >
        {renderContent()}
      </ConsoleLayout>
    </div>
  );
};

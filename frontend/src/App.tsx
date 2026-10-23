import { parseEksRoute, eksClusterPath } from './pages/eksResources';
import { parseEcsRoute, clusterPath, servicePath } from './pages/ecsResources';
import { useSession } from './components/SessionContext';
import React, { Suspense, lazy, useState, useEffect } from 'react';
import { TopNav } from './components/TopNav';
import { ConsoleLayout } from './components/ConsoleLayout';
import { ConsoleHome } from './pages/ConsoleHome';
import { useLabCompletion } from './labCompletion';
import Icon from '@cloudscape-design/components/icon';
import { AwsServiceIcon } from './components/AwsServiceIcons';
import { FavoritesBar } from './components/FavoritesBar';
import { fetchServices } from './api/client';
import { LAB_COUNT, LAB_CURRICULUM } from './labCurriculum';
import { ServiceDefinition } from './types';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Button from '@cloudscape-design/components/button';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';

// Each console page loads on first visit, so the initial bundle only carries
// the shell (navigation, layout, home page).
const CloudTrailConsole = lazy(() => import('./pages/CloudTrailConsole').then((module) => ({ default: module.CloudTrailConsole })));
const S3Console = lazy(() => import('./pages/S3Console').then((module) => ({ default: module.S3Console })));
const EC2Console = lazy(() => import('./pages/EC2Console').then((module) => ({ default: module.EC2Console })));
const IAMConsole = lazy(() => import('./pages/IAMConsole').then((module) => ({ default: module.IAMConsole })));
const DynamoDBConsole = lazy(() => import('./pages/DynamoDBConsole').then((module) => ({ default: module.DynamoDBConsole })));
const LambdaConsole = lazy(() => import('./pages/LambdaConsole').then((module) => ({ default: module.LambdaConsole })));
const SQSConsole = lazy(() => import('./pages/SQSConsole').then((module) => ({ default: module.SQSConsole })));
const SNSConsole = lazy(() => import('./pages/SNSConsole').then((module) => ({ default: module.SNSConsole })));
const RDSConsole = lazy(() => import('./pages/RDSConsole').then((module) => ({ default: module.RDSConsole })));
const KMSConsole = lazy(() => import('./pages/KMSConsole').then((module) => ({ default: module.KMSConsole })));
const SecretsManagerConsole = lazy(() => import('./pages/SecretsManagerConsole').then((module) => ({ default: module.SecretsManagerConsole })));
const CloudFormationConsole = lazy(() => import('./pages/CloudFormationConsole').then((module) => ({ default: module.CloudFormationConsole })));
const Route53Console = lazy(() => import('./pages/Route53Console').then((module) => ({ default: module.Route53Console })));
const EventBridgeConsole = lazy(() => import('./pages/EventBridgeConsole').then((module) => ({ default: module.EventBridgeConsole })));
const CloudWatchConsole = lazy(() => import('./pages/CloudWatchConsole').then((module) => ({ default: module.CloudWatchConsole })));
const StepFunctionsConsole = lazy(() => import('./pages/StepFunctionsConsole').then((module) => ({ default: module.StepFunctionsConsole })));
const CognitoConsole = lazy(() => import('./pages/CognitoConsole').then((module) => ({ default: module.CognitoConsole })));
const ApiGatewayConsole = lazy(() => import('./pages/ApiGatewayConsole').then((module) => ({ default: module.ApiGatewayConsole })));
const SSMConsole = lazy(() => import('./pages/SSMConsole').then((module) => ({ default: module.SSMConsole })));
const ECSConsole = lazy(() => import('./pages/ECSConsole').then((module) => ({ default: module.ECSConsole })));
const EKSConsole = lazy(() => import('./pages/EKSConsole').then((module) => ({ default: module.EKSConsole })));
const ECRConsole = lazy(() => import('./pages/ECRConsole').then((module) => ({ default: module.ECRConsole })));
const CloudFrontConsole = lazy(() => import('./pages/CloudFrontConsole').then((module) => ({ default: module.CloudFrontConsole })));
const ELBv2Console = lazy(() => import('./pages/ELBv2Console').then((module) => ({ default: module.ELBv2Console })));
const AthenaConsole = lazy(() => import('./pages/AthenaConsole').then((module) => ({ default: module.AthenaConsole })));
const AppSyncConsole = lazy(() => import('./pages/AppSyncConsole').then((module) => ({ default: module.AppSyncConsole })));
const SESConsole = lazy(() => import('./pages/SESConsole').then((module) => ({ default: module.SESConsole })));
const LabsConsole = lazy(() => import('./pages/LabsConsole').then((module) => ({ default: module.LabsConsole })));
const InspectorConsole = lazy(() => import('./pages/InspectorConsole').then((module) => ({ default: module.InspectorConsole })));
const SettingsConsole = lazy(() => import('./pages/SettingsConsole').then((module) => ({ default: module.SettingsConsole })));
const EnvironmentConsole = lazy(() => import('./pages/EnvironmentConsole').then((module) => ({ default: module.EnvironmentConsole })));
const ActivityConsole = lazy(() => import('./pages/ActivityConsole').then((module) => ({ default: module.ActivityConsole })));
const CliConsole = lazy(() => import('./pages/CliConsole').then((module) => ({ default: module.CliConsole })));
const ResourceGraphConsole = lazy(() => import('./pages/ResourceGraphConsole').then((module) => ({ default: module.ResourceGraphConsole })));
const GenericServiceWorkbench = lazy(() => import('./pages/GenericServiceWorkbench').then((module) => ({ default: module.GenericServiceWorkbench })));

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
  const labCompletion = useLabCompletion();
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
    if (currentView === 'console') {
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'AWS CLI', href: 'console' }];
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
    if (currentView === 'rds') {
      const query = new URLSearchParams(activeSubView.split('?')[1] || '');
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'Amazon RDS', href: 'rds' }, ...(query.get('db') || query.get('cluster') ? [{ text: (query.get('db') || query.get('cluster'))!, href: activeSubView }] : [])];
    }
    if (currentView === 'cloudformation') {
      const query = new URLSearchParams(activeSubView.split('?')[1] || '');
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'CloudFormation', href: 'cloudformation' },
        ...(query.get('stack') ? [{ text: query.get('stack')!, href: `cloudformation/overview?stack=${encodeURIComponent(query.get('stack')!)}` }] : []),
        ...(query.get('changeSet') ? [{ text: query.get('changeSet')!, href: activeSubView }] : [])];
    }
    if (currentView === 'appsync') {
      const api = new URLSearchParams(activeSubView.split('?')[1] || '').get('api');
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'AWS AppSync', href: 'appsync/apis' }, ...(api ? [{ text: api, href: activeSubView }] : [])];
    }
    if (currentView === 'eventbridge') {
      const query = new URLSearchParams(activeSubView.split('?')[1] || '');
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'Amazon EventBridge', href: 'eventbridge' },
        ...(query.get('rule') ? [{ text: `${query.get('bus') || 'default'} / ${query.get('rule')}`, href: activeSubView }] : [])];
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
    if (currentView === 'sns') {
      const topic = new URLSearchParams(activeSubView.split('?')[1] || '').get('topic');
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'Amazon SNS', href: 'sns' },
        ...(topic ? [{ text: topic.split(':').pop() || topic, href: `sns/overview?${new URLSearchParams({ topic })}` }] : [])];
    }
    if (currentView === 'stepfunctions') {
      const query = new URLSearchParams(activeSubView.split('?')[1] || '');
      const machine = query.get('machine'); const execution = query.get('execution');
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'Step Functions', href: 'stepfunctions/machines' },
        ...(machine ? [{ text: machine.split(':').pop() || machine, href: `stepfunctions/machines?${new URLSearchParams({ machine })}` }] : []),
        ...(execution ? [{ text: execution.split(':').pop() || execution, href: activeSubView }] : [])];
    }
    if (currentView === 'apigateway') {
      const api = new URLSearchParams(activeSubView.split('?')[1] || '').get('api');
      const section = activeSubView.split('?')[0].split('/')[1] || 'overview';
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'API Gateway', href: 'apigateway' },
        ...(api ? [{ text: api, href: `apigateway/overview?api=${encodeURIComponent(api)}` }, { text: section === 'resources' ? 'Routes and resources' : section.charAt(0).toUpperCase() + section.slice(1), href: activeSubView }] : [])];
    }
    if (currentView === 'cloudwatch') {
      const alarm = new URLSearchParams(activeSubView.split('?')[1] || '').get('alarm');
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'CloudWatch', href: 'cloudwatch' },
        { text: activeSubView.includes('/metrics') ? 'Metrics' : activeSubView.includes('/logs') ? 'Log groups' : activeSubView.includes('/dashboards') ? 'Dashboards' : 'Alarms', href: activeSubView.split('?')[0] }, ...(alarm ? [{ text: alarm, href: activeSubView }] : [])];
    }
    if (currentView === 'secretsmanager') {
      const secret = new URLSearchParams(activeSubView.split('?')[1] || '').get('secret');
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'Secrets Manager', href: 'secretsmanager' },
        ...(secret ? [{ text: secret, href: activeSubView }] : [])];
    }
    if (currentView === 'ses') {
      const identity = new URLSearchParams(activeSubView.split('?')[1] || '').get('identity');
      return [
        { text: 'AWS Management Console', href: 'home' },
        { text: 'Amazon SES', href: 'ses/overview' },
        ...(identity ? [{ text: 'Identities', href: 'ses/identities' }, { text: identity, href: `ses/details?identity=${encodeURIComponent(identity)}` }] : []),
      ];
    }
    if (currentView === 'ssm') {
      const query = new URLSearchParams(activeSubView.split('?')[1] || '');
      const parameter = query.get('parameter');
      return [
        { text: 'AWS Management Console', href: 'home' },
        { text: 'Systems Manager', href: 'ssm/parameters' },
        { text: 'Parameter Store', href: `ssm/parameters${query.get('path') ? `?path=${encodeURIComponent(query.get('path')!)}` : ''}` },
        ...(parameter ? [{ text: parameter, href: `ssm/details?parameter=${encodeURIComponent(parameter)}` }] : []),
      ];
    }
    if (currentView === 'kms') {
      const key = new URLSearchParams(activeSubView.split('?')[1] || '').get('key');
      return [
        { text: 'AWS Management Console', href: 'home' },
        { text: 'AWS KMS', href: 'kms/keys' },
        ...(key ? [{ text: key, href: `kms/details?key=${encodeURIComponent(key)}` }] : []),
      ];
    }
    if (currentView === 'ecr') {
      const query = new URLSearchParams(activeSubView.split('?')[1] || '');
      const repository = query.get('repository');
      return [
        { text: 'AWS Management Console', href: 'home' },
        { text: 'Amazon ECR', href: 'ecr/repositories' },
        ...(repository ? [{ text: repository, href: `ecr/images?repository=${encodeURIComponent(repository)}` }] : []),
        ...(query.get('image') ? [{ text: 'Image details', href: activeSubView }] : []),
      ];
    }
    if (currentView === 'cloudtrail') {
      const query = new URLSearchParams(activeSubView.split('?')[1] || '');
      const trail = query.get('trail');
      return [{ text: 'AWS Management Console', href: 'home' }, { text: 'CloudTrail', href: 'cloudtrail/events' }, { text: trail ? 'Trails' : 'Event history', href: trail ? 'cloudtrail/trails' : 'cloudtrail/events' }, ...(trail ? [{ text: trail, href: activeSubView }] : query.get('event') ? [{ text: query.get('event')!, href: activeSubView }] : [])];
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
      labs: `AWS Workflow Labs (${LAB_COUNT})`,
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
        { type: 'link' as const, text: `Workflow Labs (${LAB_COUNT})`, href: 'labs', icon: <AwsServiceIcon service="labs" size={18} /> },
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
              { type: 'link' as const, text: 'Key Pairs', href: 'ec2/key_pairs' },
              { type: 'link' as const, text: 'Virtual Private Clouds (VPC)', href: 'ec2/vpcs' },
              { type: 'link' as const, text: 'Subnets', href: 'ec2/subnets' },
              { type: 'link' as const, text: 'Route tables', href: 'ec2/routes' },
              { type: 'link' as const, text: 'Gateways and endpoints', href: 'ec2/gateways' },
              { type: 'link' as const, text: 'Elastic IPs', href: 'ec2/elastic_ips' },
              { type: 'link' as const, text: 'NAT gateways', href: 'ec2/nat_gateways' },
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
              { type: 'link' as const, text: 'Account settings', href: 'iam/account' },
            ],
          },
          {
            type: 'section' as const,
            text: 'Access Analysis',
            items: [
              { type: 'link' as const, text: 'Credential report', href: 'iam/credential_report' },
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
      case 'sns': {
        const topic = new URLSearchParams(activeSubView.split('?')[1] || '').get('topic');
        const query = topic ? `?topic=${encodeURIComponent(topic)}` : '';
        return [
          { type: 'link' as const, text: 'Topics', href: 'sns', icon: <AwsServiceIcon service="sns" size={18} /> },
          ...(topic ? [
            { type: 'link' as const, text: 'Message fan-out', href: `sns/overview${query}` },
            { type: 'link' as const, text: 'Subscriptions', href: `sns/subscriptions${query}` },
            { type: 'link' as const, text: 'Topic configuration', href: `sns/details${query}` },
            { type: 'link' as const, text: 'Filter policies', href: `sns/filtering${query}` },
            { type: 'link' as const, text: 'Access policy', href: `sns/policy${query}` },
          ] : []),
          { type: 'divider' as const }, { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      }
      case 'rds': {
        const query = new URLSearchParams(activeSubView.split('?')[1] || '');
        const db = query.get('db'), cluster = query.get('cluster');
        const resource = db || cluster;
        const base = db ? `rds/instances?db=${encodeURIComponent(db)}` : cluster ? `rds/clusters?cluster=${encodeURIComponent(cluster)}` : '';
        return [
          { type: 'link' as const, text: 'Databases', href: 'rds/instances', icon: <AwsServiceIcon service="rds" size={18} /> },
          { type: 'link' as const, text: 'Clusters', href: 'rds/clusters' },
          { type: 'link' as const, text: 'Parameter groups', href: 'rds/parameter-groups' },
          { type: 'link' as const, text: 'Snapshots', href: 'rds/snapshots' },
          ...(resource ? [
            { type: 'divider' as const },
            { type: 'link' as const, text: 'Overview', href: `${base}&tab=overview` },
            ...(db ? [{ type: 'link' as const, text: 'Connectivity', href: `${base}&tab=connectivity` }, { type: 'link' as const, text: 'Configuration', href: `${base}&tab=configuration` }] : [{ type: 'link' as const, text: 'Members and endpoints', href: `${base}&tab=members` }]),
          ] : []),
          { type: 'divider' as const }, { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      }
      case 'kms': {
        const key = new URLSearchParams(activeSubView.split('?')[1] || '').get('key');
        const query = key ? `?key=${encodeURIComponent(key)}` : '';
        return [
          { type: 'link' as const, text: 'Keys', href: 'kms/keys', icon: <AwsServiceIcon service="kms" size={18} /> },
          ...(key ? [
            { type: 'link' as const, text: 'Cryptographic configuration', href: `kms/details${query}` },
            { type: 'link' as const, text: 'Key policy', href: `kms/policy${query}` },
            { type: 'link' as const, text: 'Key rotation', href: `kms/rotation${query}` },
            { type: 'link' as const, text: 'Grants', href: `kms/grants${query}` },
            { type: 'link' as const, text: 'Tags', href: `kms/tags${query}` },
            { type: 'link' as const, text: 'Cryptographic tools', href: `kms/tools${query}` },
          ] : []),
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      }
      case 'secretsmanager':
        return [
          { type: 'link' as const, text: 'Secrets', href: 'secretsmanager', icon: <AwsServiceIcon service="secretsmanager" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'cloudformation': {
        const stack = new URLSearchParams(activeSubView.split('?')[1] || '').get('stack');
        const query = stack ? `?stack=${encodeURIComponent(stack)}` : '';
        return [
          { type: 'link' as const, text: 'Stacks', href: 'cloudformation', icon: <AwsServiceIcon service="cloudformation" size={18} /> },
          ...(stack ? [
            { type: 'link' as const, text: 'Overview', href: `cloudformation/overview${query}` },
            { type: 'link' as const, text: 'Events', href: `cloudformation/events${query}` },
            { type: 'link' as const, text: 'Resources', href: `cloudformation/resources${query}` },
            { type: 'link' as const, text: 'Outputs and parameters', href: `cloudformation/outputs_params${query}` },
            { type: 'link' as const, text: 'Change sets', href: `cloudformation/change_sets${query}` },
            { type: 'link' as const, text: 'Template', href: `cloudformation/template${query}` },
          ] : []),
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      }
      case 'cloudtrail': {
        const trail = new URLSearchParams(activeSubView.split('?')[1] || '').get('trail');
        return [
          { type: 'link' as const, text: 'Event history', href: 'cloudtrail/events', icon: <AwsServiceIcon service="cloudtrail" size={18} /> },
          { type: 'link' as const, text: 'Trails', href: 'cloudtrail/trails' },
          ...(trail ? [{ type: 'link' as const, text: 'Trail overview', href: `cloudtrail/trails?${new URLSearchParams({ trail, tab: 'overview' })}` }, { type: 'link' as const, text: 'Event selectors', href: `cloudtrail/trails?${new URLSearchParams({ trail, tab: 'selectors' })}` }] : []),
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      }
      case 'route53':
        return [
          { type: 'link' as const, text: 'Hosted Zones & DNS Records', href: 'route53', icon: <AwsServiceIcon service="route53" size={18} /> },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      case 'eventbridge': {
        const query = activeSubView.includes('?') ? `?${activeSubView.split('?')[1]}` : '';
        return [
          { type: 'link' as const, text: 'Rules', href: 'eventbridge/rules', icon: <AwsServiceIcon service="eventbridge" size={18} /> },
          { type: 'link' as const, text: 'Event buses', href: 'eventbridge/buses' },
          { type: 'link' as const, text: 'Pattern tester', href: `eventbridge/tester${query}` },
          { type: 'link' as const, text: 'Send events', href: `eventbridge/send${query}` },
          { type: 'link' as const, text: 'Archives and replays', href: 'eventbridge/archives' },
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      }
      case 'cloudwatch':
        return [
          { type: 'link' as const, text: 'Alarms', href: 'cloudwatch/alarms', icon: <AwsServiceIcon service="cloudwatch" size={18} /> },
          { type: 'link' as const, text: 'Metrics', href: 'cloudwatch/metrics' },
          { type: 'link' as const, text: 'Dashboards', href: 'cloudwatch/dashboards' },
          { type: 'link' as const, text: 'Log groups', href: 'cloudwatch/logs' },
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
      case 'apigateway': {
        const api = new URLSearchParams(activeSubView.split('?')[1] || '').get('api');
        const query = api ? `?api=${encodeURIComponent(api)}` : '';
        return [
          { type: 'link' as const, text: 'APIs', href: 'apigateway', icon: <AwsServiceIcon service="apigateway" size={18} /> },
          ...(api ? [
            { type: 'link' as const, text: 'Overview', href: `apigateway/overview${query}` },
            { type: 'link' as const, text: 'Routes and resources', href: `apigateway/resources${query}` },
            { type: 'link' as const, text: 'Integrations', href: `apigateway/integrations${query}` },
            { type: 'link' as const, text: 'Stages and deployments', href: `apigateway/stages${query}` },
            { type: 'link' as const, text: 'Authorizers', href: `apigateway/authorizers${query}` },
            { type: 'link' as const, text: 'Test request', href: `apigateway/test${query}` },
          ] : []),
          { type: 'divider' as const }, { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      }
      case 'ssm':
        return [
          { type: 'link' as const, text: 'Parameter Store', href: 'ssm/parameters', icon: <AwsServiceIcon service="ssm" size={18} /> },
          { type: 'link' as const, text: 'Documents', href: 'ssm/documents' },
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
      case 'ecr': {
        const repository = new URLSearchParams(activeSubView.split('?')[1] || '').get('repository');
        const query = repository ? `?repository=${encodeURIComponent(repository)}` : '';
        return [
          { type: 'link' as const, text: 'Private repositories', href: 'ecr/repositories', icon: <AwsServiceIcon service="ecr" size={18} /> },
          ...(repository ? [
            { type: 'link' as const, text: 'Images', href: `ecr/images${query}` },
            { type: 'link' as const, text: 'Lifecycle policy & settings', href: `ecr/lifecycle${query}` },
            { type: 'link' as const, text: 'Permissions', href: `ecr/permissions${query}` },
            { type: 'link' as const, text: 'Tags', href: `ecr/tags${query}` },
          ] : []),
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      }
      case 'cloudfront':
        return [
          { type: 'link' as const, text: 'Distributions', href: 'cloudfront/distributions', icon: <AwsServiceIcon service="cloudfront" size={18} /> },
          { type: 'link' as const, text: 'Invalidations', href: 'cloudfront/invalidations' },
          { type: 'link' as const, text: 'Origins & Behaviors', href: 'cloudfront/origins' },
          { type: 'link' as const, text: 'Cache Policies', href: 'cloudfront/cache-policies' },
          { type: 'link' as const, text: 'Origin access', href: 'cloudfront/origin-access' },
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
          { type: 'link' as const, text: 'Health checks', href: 'elasticloadbalancing/healthchecks' },
          { type: 'link' as const, text: 'Attributes', href: 'elasticloadbalancing/attributes' },
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
      case 'appsync': {
        const api = new URLSearchParams(activeSubView.split('?')[1] || '').get('api');
        const query = api ? `?api=${encodeURIComponent(api)}` : '';
        return [
          { type: 'link' as const, text: 'APIs', href: 'appsync/apis', icon: <AwsServiceIcon service="appsync" size={18} /> },
          ...(api ? [
            { type: 'link' as const, text: 'Overview', href: `appsync/overview${query}` },
            { type: 'link' as const, text: 'Schema', href: `appsync/schema${query}` },
            { type: 'link' as const, text: 'Data sources', href: `appsync/datasources${query}` },
            { type: 'link' as const, text: 'Resolvers', href: `appsync/resolvers${query}` },
            { type: 'link' as const, text: 'API keys', href: `appsync/apikeys${query}` },
            { type: 'link' as const, text: 'Queries', href: `appsync/sandbox${query}` },
          ] : []),
          { type: 'divider' as const },
          { type: 'link' as const, text: '← Console Home', href: 'home' },
        ];
      }
      case 'ses':
        return [
          { type: 'link' as const, text: 'Account dashboard', href: 'ses/overview' },
          { type: 'link' as const, text: 'Identities', href: 'ses/identities', icon: <AwsServiceIcon service="ses" size={18} /> },
          { type: 'link' as const, text: 'Send test email', href: 'ses/send' },
          { type: 'link' as const, text: 'Email Templates', href: 'ses/templates' },
          { type: 'link' as const, text: 'Configuration Sets', href: 'ses/configsets' },
          { type: 'link' as const, text: 'Local test mailbox', href: 'ses/mailbox' },
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
            text: `All Labs Overview (${LAB_COUNT})`,
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
              info: labCompletion[`${lab.service}:${lab.key}`] ? (
                <span title="Completed" aria-label="Completed">
                  <Icon name="status-positive" variant="success" />
                </span>
              ) : undefined,
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
        return <RDSConsole resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'kms':
        return (
          <KMSConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeTabOverride['kms']}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, kms: tab }));
              setActiveSubView(`kms/${tab}`);
            }}
          />
        );
      case 'secretsmanager':
        return <SecretsManagerConsole resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'cloudformation':
        return <CloudFormationConsole resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'route53':
        return <Route53Console resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'eventbridge':
        return <EventBridgeConsole resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'cloudwatch':
        return (
          <CloudWatchConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
            activeTab={activeSubView.split('?')[0].split('/')[1] || 'alarms'}
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
            activeTab={activeSubView.split('?')[0].split('/')[1] || 'machines'}
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
            activeTab={activeSubView.split('?')[0].split('/')[1] || 'overview'}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, apigateway: tab }));
              setActiveSubView(`apigateway/${tab}`);
            }}
          />
        );
      case 'ssm':
        return (
          <SSMConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
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
            resourcePath={activeSubView}
            onNavigate={navigateTo}
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
            activeTab={activeSubView.split('?')[0].split('/')[1] || 'editor'}
            onTabChange={(tab) => {
              setActiveTabOverride((prev) => ({ ...prev, athena: tab }));
              setActiveSubView(`athena/${tab}`);
            }}
          />
        );
      case 'cloudtrail':
        return <CloudTrailConsole resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'appsync':
        return <AppSyncConsole resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'ses':
        return (
          <SESConsole
            resourcePath={activeSubView}
            onNavigate={navigateTo}
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
        return <EnvironmentConsole resourcePath={activeSubView} onNavigate={navigateTo} />;
      case 'activity':
        return <ActivityConsole onNavigateService={(s) => navigateTo(s)} />;
      case 'console':
        return <CliConsole onNavigate={navigateTo} />;
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

  // The inventory (GET /api/<service>/) whose emulation notes the layout shows.
  const inventoryView = currentView === 'elbv2' || currentView === 'elb' ? 'elasticloadbalancing' : currentView;
  const inventoryService = services.some((s) => s.key === inventoryView) ? inventoryView : undefined;

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
        inventoryService={inventoryService}
      >
        <Suspense
          fallback={
            <Box padding="l" textAlign="center">
              <StatusIndicator type="loading">Loading console</StatusIndicator>
            </Box>
          }
        >
          {renderContent()}
        </Suspense>
      </ConsoleLayout>
    </div>
  );
};

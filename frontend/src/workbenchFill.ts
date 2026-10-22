/**
 * Works out which workbench form fields a selected inventory resource can fill.
 *
 * Inventory rows and action fields use different names (a stream row has `name`, the
 * action path uses `{stream}`), and a resource of one type must never fill an action
 * aimed at another type (an Auto Scaling group name must not land in "Delete launch
 * configuration"). So a field is filled only from:
 * - the selected resource itself, when the resource type matches the field
 *   (`streams` row -> `{stream}`, `config_rules` row -> `{rule}` via `ConfigRuleName`), or
 * - a reference the row carries to its parent (`application_name` on a deployment group
 *   row -> `{application}`).
 */
import type { ActionField, ServiceAction } from './types';

const singular = (key: string) => {
  if (/ies$/.test(key)) return key.replace(/ies$/, 'y');
  if (/(sses|xes|ches|shes)$/.test(key)) return key.replace(/es$/, '');
  return key.replace(/s$/, '');
};
const baseName = (field: string) => field.replace(/_(id|name|arn)$/, '');
const pascal = (snake: string) => snake.split('_').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join('');
const scalar = (value: unknown): value is string | number => typeof value === 'string' || typeof value === 'number';

/** True when rows of `collection` are the resource that `field` identifies. */
export function collectionMatchesField(collection: string, field: string) {
  const type = singular(collection);
  const base = baseName(field);
  return type === base || type.endsWith(`_${base}`);
}

function ownValue(resource: Record<string, any>, collection: string, field: string) {
  const type = pascal(singular(collection));
  const suffix = field.match(/_(id|name|arn)$/)?.[1];
  const candidates = suffix === 'arn'
    ? ['arn', 'ARN', `${type}Arn`, `${type}ARN`]
    : suffix === 'name'
      ? ['name', `${type}Name`, 'Name']
      : ['id', 'name', `${type}Name`, `${type}Id`, 'Name', 'Id'];
  for (const key of [field, ...candidates]) if (scalar(resource[key])) return resource[key];
  return undefined;
}

function parentValue(resource: Record<string, any>, field: string) {
  const camel = field.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase());
  for (const key of [field, `${field}_name`, `${field}_id`, `${field}_arn`, `${camel}Name`, `${camel}Id`, `${camel}Arn`]) {
    if (scalar(resource[key])) return resource[key];
  }
  return undefined;
}

const isBare = (field: string) => /^(name|arn|id)$/.test(field);
const isIdentifier = (field: string, pathParams: string[]) => pathParams.includes(field) || /(_id|_name|_arn)$|^(name|arn|id)$/.test(field);

/** Values from `resource` (a row of `collection`) for the fields of `action`. */
export function resourceFieldValues(
  action: ServiceAction & { fields: ActionField[] },
  resource: Record<string, any> | null,
  collection: string,
): Record<string, string | number> {
  if (!resource) return {};
  const pathParams = (action.path.match(/\{([^}]+)\}/g) || []).map(param => param.slice(1, -1));
  const target = pathParams[pathParams.length - 1];
  // Changing or deleting: only when the selected resource is the one the action acts on,
  // never its parent (selecting a user must not fill "Delete server").
  if ((action.kind === 'delete' || action.kind === 'update') && target && !collectionMatchesField(collection, target) && !isBare(target)) return {};
  const aboutCollection = action.name.includes(singular(collection)) || /tag/.test(action.name);
  const values: Record<string, string | number> = {};
  for (const field of action.fields) {
    if (!isIdentifier(field.name, pathParams)) continue;
    const isPath = pathParams.includes(field.name);
    // A create form names a new resource; only its parent identifiers come from the selection.
    if (action.kind === 'create' && !isPath) continue;
    // A bare "name" or "arn" says nothing about its resource type, so use it only when the
    // action is about the selected type (delete_pipeline on a pipeline, tagging anything).
    if (isBare(field.name) && !aboutCollection) continue;
    const own = collectionMatchesField(collection, field.name);
    const value = own
      ? ownValue(resource, collection, field.name)
      : parentValue(resource, field.name);
    if (value !== undefined) values[field.name] = value;
  }
  return values;
}

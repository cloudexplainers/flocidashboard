"""Execute and verify every lab against an explicitly selected disposable Floci."""

import os
import time
from pathlib import Path
import json

from django.core.management.base import BaseCommand, CommandError

from dashboard.aws import FlociClientFactory
from dashboard.labs import lab_status, reset_lab, run_lab_step
from dashboard.labs.registry import all_labs


class Command(BaseCommand):
    help = 'Run lab steps, live completion checks, and resets on a disposable emulator.'

    def add_arguments(self, parser):
        parser.add_argument('--endpoint', required=True, help='Disposable local Floci URL; labs use fixed resource names.')
        parser.add_argument('--report', default='test-results/labs.json')
        parser.add_argument('--service', help='Limit checks to one service.')
        parser.add_argument('--lab', help='Limit checks to one lab key (requires --service).')

    def handle(self, *args, **options):
        if options['lab'] and not options['service']:
            raise CommandError('--lab requires --service')
        endpoint = options['endpoint']
        FlociClientFactory(endpoint_url=endpoint)  # Validate local endpoint before any writes.
        os.environ['FLOCI_AWS_ENDPOINT_URL'] = endpoint
        labs = [lab for lab in all_labs()
                if (not options['service'] or lab['service'] == options['service'])
                and (not options['lab'] or lab['key'] == options['lab'])]
        if not labs:
            raise CommandError('No matching labs')
        health = FlociClientFactory().health()
        if not health['ok']:
            raise CommandError(f'Floci is unavailable: {health}')
        report = {'endpoint': endpoint, 'version': health.get('data', {}).get('version'), 'labs': []}
        path = Path(options['report'])
        path.parent.mkdir(parents=True, exist_ok=True)
        failures = 0
        self.stdout.write(f'Checking {len(labs)} labs on disposable endpoint {endpoint}')
        for lab in labs:
            result = {'service': lab['service'], 'lab': lab['key'], 'steps': [], 'passed': True}
            self.stdout.write(f"LAB {lab['service']}/{lab['key']}")
            try:
                for step in lab['steps']:
                    started = time.monotonic()
                    item = {'step': step['key']}
                    try:
                        data = run_lab_step(lab['service'], lab['key'], step['key'])
                        item['verified'] = bool(data.get('verified'))
                        item['verification'] = data.get('verification')
                    except Exception as exc:
                        item.update(verified=False, error=str(exc))
                    item['seconds'] = round(time.monotonic() - started, 3)
                    result['steps'].append(item)
                    if not item['verified']:
                        result['passed'] = False
                    self.stdout.write(f"  {'PASS' if item['verified'] else 'FAIL'} {step['key']}")
                    # Save incrementally so interrupted runs retain completed checks.
                    path.write_text(json.dumps({**report, 'labs': report['labs'] + [result]}, indent=2, default=str) + '\n')
                status = lab_status(lab['service'], lab['key'])
                result['complete'] = bool(status.get('complete'))
                if not result['complete']:
                    result['passed'] = False
                    result['status'] = status
            except Exception as exc:
                result.update(passed=False, error=str(exc))
            finally:
                try:
                    reset = reset_lab(lab['service'], lab['key'])
                    result['reset'] = reset
                    if reset.get('error') or reset.get('errors'):
                        result['passed'] = False
                except Exception as exc:
                    result.update(passed=False, reset_error=str(exc))
                report['labs'].append(result)
                failures += not result['passed']
                report['failed'] = failures
                path.write_text(json.dumps(report, indent=2, default=str) + '\n')
        self.stdout.write(f'{len(labs) - failures}/{len(labs)} labs passed; report: {path.resolve()}')
        if failures:
            raise CommandError(f'{failures} labs failed live verification or cleanup')

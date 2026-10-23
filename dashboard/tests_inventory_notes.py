"""Inventory notes: what every console shows under "How Floci emulates this service"."""
import ast
import re
from pathlib import Path

from django.test import SimpleTestCase

AWS_DIR = Path(__file__).resolve().parent / 'aws'


def inventory_notes() -> dict[str, list[str]]:
    """The literal `notes` lists of every inventory loader, by module name."""
    found: dict[str, list[str]] = {}
    for path in sorted(AWS_DIR.glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == 'notes' and isinstance(value, ast.List):
                    found.setdefault(path.stem, []).extend(
                        item.value for item in value.elts
                        if isinstance(item, ast.Constant) and isinstance(item.value, str))
    return found


class InventoryNoteTextTests(SimpleTestCase):

    def test_notes_carry_no_implementation_chatter(self):
        chatter = re.compile(
            r'^(This page focuses|This read-only inspector|The dashboard (samples|inspects|intentionally)'
            r'|.* (round out|complements) |.* uses (JSON 1\.1|Query XML|a REST-JSON API))')
        offenders = [f'{module}: {note}' for module, notes in inventory_notes().items()
                     for note in notes if chatter.match(note)]
        self.assertEqual(offenders, [])

    def test_ec2_notes_describe_real_key_pairs_and_opt_in_security_groups(self):
        notes = ' '.join(inventory_notes()['ec2'])
        self.assertNotIn('dummy', notes)
        self.assertIn('CreateKeyPair returns a usable private key', notes)
        self.assertIn('FLOCI_NETWORK_SECURITY_GROUP_ENFORCEMENT_ENABLED=true', notes)

    def test_pipes_notes_list_the_targets_floci_delivers_to(self):
        notes = ' '.join(inventory_notes()['pipes'])
        self.assertIn('EventBridge event buses', notes)
        self.assertNotIn('Kinesis, and Step Functions', notes)

    def test_eks_and_athena_notes_describe_current_behavior(self):
        notes = inventory_notes()
        self.assertNotIn('Phase 1', ' '.join(notes['eks']))
        self.assertIn('DuckDB', ' '.join(notes['athena']))

    def test_notes_do_not_repeat_what_the_console_shows_inline(self):
        # Each of these consoles shows the note next to the setting it describes (FlociNote).
        inline = {
            'dynamodb': ['Point-in-time recovery'],
            'cloudfront': ['without caching'],
            'secretsmanager': ['Resource policies'],
            'rds': ['pg_dumpall', 'ApplyImmediately', 'transaction log', 'read replica', 'failover'],
            'route53': ['global service'],
            'cloudtrail': ['management events', 'AWSLogs/'],
        }
        notes = inventory_notes()
        repeated = [f'{module}: {text}' for module, texts in inline.items()
                    for text in texts if text in ' '.join(notes.get(module, []))]
        self.assertEqual(repeated, [])

    def test_kept_notes_still_describe_unsimulated_behavior(self):
        notes = inventory_notes()
        self.assertTrue(any('replicates nothing' in note for note in notes['dynamodb']))
        self.assertTrue(any('rotation schedule' in note for note in notes['secretsmanager']))
        self.assertTrue(any('not evaluated' in note for note in notes['kms']))

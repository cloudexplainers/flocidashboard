import os
import sys
import threading

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard'

    def ready(self):
        # Building every lab's boto3 and Terraform snippets takes several seconds (each AWS CLI
        # command goes through the CLI's own parser), so the dev server warms that cache in the
        # background instead of making the first Labs page load wait for it.
        serving = 'runserver' in sys.argv and (os.environ.get('RUN_MAIN') == 'true' or '--noreload' in sys.argv)
        if serving:
            threading.Thread(target=_warm_lab_snippets, name='warm-lab-snippets', daemon=True).start()


def _warm_lab_snippets():
    from .labs.registry import all_labs
    from .labs.snippets import get_step_snippets

    for lab in all_labs():
        for step in lab.get('steps', []):
            try:
                get_step_snippets(step, lab.get('service'))
            except Exception:  # a bad snippet must never take the server down
                pass

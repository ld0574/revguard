import os
import sys

sys.path.insert(0, os.getcwd())

from pydantic import ValidationError

from revguard.api import ApprovalDecision

print("allowed_fields=", sorted(ApprovalDecision.model_fields))
try:
    ApprovalDecision(decision="APPROVED", matrix_event_id="forged")
except ValidationError:
    print("extra_field_rejected=ValidationError")
else:
    print("extra_field_rejected=None(!!)")

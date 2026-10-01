from django import forms

from apps.requisitions.services import DECISION_CONFIRM_NEED, DECISION_REJECT_NEED


class DepartmentNeedReviewForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            (DECISION_CONFIRM_NEED, "Need confirmed"),
            (DECISION_REJECT_NEED, "Need not justified"),
        ]
    )
    comments = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": "form-control",
                "placeholder": "Document the approval decision. Required when rejecting.",
            }
        ),
    )

    def clean(self):
        cleaned = super().clean()
        decision = cleaned.get("decision")
        comments = (cleaned.get("comments") or "").strip()
        cleaned["comments"] = comments
        if decision == DECISION_REJECT_NEED and not comments:
            self.add_error(
                "comments",
                "Document the approval request: a rejection reason is required.",
            )
        return cleaned

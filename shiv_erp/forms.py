from decimal import Decimal

from django import forms

from .models import Budget, BudgetPeriod, Payment


class BudgetReportForm(forms.Form):
    period = forms.ModelChoiceField(queryset=BudgetPeriod.objects.filter(is_active=True), required=False)
    kind = forms.ChoiceField(choices=[("", "All")] + list(Budget.Kind.choices), required=False)


class QuickEntryForm(forms.Form):
    period = forms.ModelChoiceField(queryset=BudgetPeriod.objects.filter(is_active=True), required=False)
    kind = forms.ChoiceField(choices=Budget.Kind.choices)
    cost_center_name = forms.CharField(max_length=255)
    cost_center_code = forms.CharField(max_length=50, required=False)
    budget_amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.00"))
    actual_amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.00"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["period"].widget.attrs.update({"class": "form-select"})
        self.fields["kind"].widget.attrs.update({"class": "form-select"})
        self.fields["cost_center_name"].widget.attrs.update({"class": "form-control"})
        self.fields["cost_center_code"].widget.attrs.update({"class": "form-control"})
        self.fields["budget_amount"].widget.attrs.update({"class": "form-control", "step": "0.01"})
        self.fields["actual_amount"].widget.attrs.update({"class": "form-control", "step": "0.01"})


class PortalPaymentForm(forms.Form):
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    method = forms.ChoiceField(choices=Payment.Method.choices, initial=Payment.Method.ONLINE)

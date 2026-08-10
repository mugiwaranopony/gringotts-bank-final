from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import LoanApplicationForm, LoanRepaymentForm
from .models import (
    LoanAccountMissing,
    LoanApplication,
    LoanInsufficientFunds,
    LoanRepaymentError,
    RepaymentExceedsRemaining,
    make_loan_repayment,
)


def lending_rates():
    term_labels = dict(LoanApplication.REPAYMENT_TERM_CHOICES)
    return [
        {
            "term": term,
            "term_label": term_labels[term],
            "rate_percent": rate * 100,
        }
        for term, rate in LoanApplication.INTEREST_RATES_BY_TERM.items()
    ]


@login_required
def loans_page(request):
    if request.method == "POST":
        form = LoanApplicationForm(request.POST)
        if form.is_valid():
            application = form.save(commit=False)
            application.user = request.user
            application.status = LoanApplication.PENDING
            application.save()
            messages.success(
                request,
                "Your Gringotts loan application has been submitted for review.",
            )
            messages.info(
                request,
                "Preliminary Goblin Risk Assessment: "
                "Concerning, but not unusually so.",
            )
            return redirect("loans")
    else:
        form = LoanApplicationForm()

    applications = LoanApplication.objects.filter(user=request.user)
    return render(
        request,
        "loans/loan_application.html",
        {
            "applications": applications,
            "form": form,
            "lending_rates": lending_rates(),
        },
    )


@login_required
@require_POST
def repay_loan(request, pk):
    application = get_object_or_404(
        LoanApplication,
        pk=pk,
        user=request.user,
    )
    form = LoanRepaymentForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            "Repayment amount must be greater than zero and use no more "
            "than two decimal places.",
            extra_tags="danger",
        )
        return redirect("loans")

    amount = form.cleaned_data["amount"]
    try:
        application = make_loan_repayment(
            application.pk,
            request.user,
            amount,
        )
    except LoanApplication.DoesNotExist as exc:
        raise Http404("No matching loan application.") from exc
    except RepaymentExceedsRemaining as exc:
        messages.error(
            request,
            "Repayment cannot exceed the remaining balance of "
            f"${exc.remaining_balance:,.2f}.",
            extra_tags="danger",
        )
    except LoanInsufficientFunds as exc:
        messages.error(
            request,
            "Insufficient balance. This repayment requires "
            f"${exc.required:,.2f}, but your available balance is "
            f"${exc.available:,.2f}.",
            extra_tags="danger",
        )
    except (LoanAccountMissing, LoanRepaymentError) as exc:
        messages.error(request, str(exc), extra_tags="danger")
    else:
        if application.is_fully_repaid:
            messages.success(
                request,
                f"Payment successful! ${amount:,.2f} was applied to Loan "
                f"#{application.pk}. The loan is now paid in full.",
            )
        else:
            messages.success(
                request,
                f"Payment successful! ${amount:,.2f} was applied to Loan "
                f"#{application.pk}. Remaining balance: "
                f"${application.remaining_balance:,.2f}.",
            )

    return redirect("loans")

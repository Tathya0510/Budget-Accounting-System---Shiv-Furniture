from django.urls import path

from . import views


urlpatterns = [
    path("", views.home_redirect, name="home"),
    path("login/", views.login_choice, name="login"),
    path(
        "customer/login/",
        views.customer_login_view,
        name="customer_login",
    ),
    path("logout/", views.logout_view, name="logout"),
    path("portal/", views.portal_dashboard, name="portal_dashboard"),
    path("portal/documents/", views.portal_documents, name="portal_documents"),
    path("portal/documents/<str:doc_type>/", views.portal_documents, name="portal_documents_by_type"),
    path("portal/document/<str:number>/", views.portal_document_detail, name="portal_document_detail"),
    path("portal/document/<str:number>/pdf/", views.portal_document_pdf, name="portal_document_pdf"),
    path("portal/document/<str:number>/pay/", views.portal_pay_document, name="portal_pay_document"),
    path("reports/budget/", views.budget_report, name="budget_report"),
]

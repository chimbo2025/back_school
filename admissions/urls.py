from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import GradeViewSet, FeeGroupViewSet, FeeItemViewSet, StudentViewSet, PaymentViewSet, FinanceViewSet, me_view
from .verification import ClaimViewSet, StatementViewSet, AllocationViewSet, AuditViewSet, SettingsViewSet

router = DefaultRouter()
router.register(r'grades', GradeViewSet, basename='grade')
router.register(r'fee-groups', FeeGroupViewSet, basename='fee-group')
router.register(r'fee-items', FeeItemViewSet, basename='fee-item')
router.register(r'students', StudentViewSet, basename='student')
router.register(r'payments', PaymentViewSet, basename='payment')
router.register(r'finance', FinanceViewSet, basename='finance')
router.register(r'claims', ClaimViewSet, basename='claim')
router.register(r'statements', StatementViewSet, basename='statement')
router.register(r'allocations', AllocationViewSet, basename='allocation')
router.register(r'audit', AuditViewSet, basename='audit')
router.register(r'settings', SettingsViewSet, basename='setting')

urlpatterns = [
    path('', include(router.urls)),
    path('auth/login/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/me/', me_view, name='user_me'),
]

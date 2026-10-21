from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from review import api, web

router = DefaultRouter()
router.register("studies", api.StudyViewSet, basename="study")
router.register("images", api.ImageViewSet, basename="image")
router.register("tasks", api.TaskViewSet, basename="task")
router.register("annotations", api.AnnotationViewSet, basename="annotation")
router.register("labels", api.LabelViewSet, basename="label")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path("api/audit/", api.AuditLogView.as_view(), name="audit-log"),
    path("api/audit/verify/", api.AuditVerifyView.as_view(), name="audit-verify"),
    path("login/", auth_views.LoginView.as_view(template_name="review/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", web.study_list, name="studies"),
    path("studies/<int:pk>/", web.study_detail, name="study"),
    path("images/<int:pk>/annotate/", web.annotate, name="annotate"),
    path("review/", web.review_queue, name="review-queue"),
    path("review/<int:pk>/", web.review_task, name="review-task"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

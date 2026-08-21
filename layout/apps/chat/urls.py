from django.urls import path
from . import views

app_name = "chat"

urlpatterns = [
    path("", views.chat_view, name="index"),
    path("settings/", views.settings_view, name="settings"),
    path("api/chat/", views.chat_api, name="api"),
    path("api/upload/", views.upload_file, name="upload"),
    path("api/download/", views.download_file, name="download"),
    path("api/settings/", views.settings_api, name="settings_api"),
    path("api/tool-detail/", views.tool_detail_api, name="tool_detail_api"),
    path("api/progress/", views.progresso_api, name="progress_api"),
    path("api/save-agent-models/", views.save_agent_models, name="save_agent_models"),
]

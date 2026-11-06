from django.urls import path

from . import agent_views

urlpatterns = [
    path("enroll/", agent_views.AgentEnrollView.as_view(), name="agent-enroll"),
    path("heartbeat/", agent_views.AgentHeartbeatView.as_view(), name="agent-heartbeat"),
    path("rotate/", agent_views.AgentRotateView.as_view(), name="agent-rotate"),
    path("observations/", agent_views.AgentObservationView.as_view(), name="agent-observations"),
    path("backups/", agent_views.AgentBackupView.as_view(), name="agent-backups"),
    path("commands/lease/", agent_views.AgentCommandLeaseView.as_view(), name="agent-lease"),
    path(
        "commands/<uuid:pk>/events/",
        agent_views.AgentCommandEventView.as_view(),
        name="agent-command-events",
    ),
]

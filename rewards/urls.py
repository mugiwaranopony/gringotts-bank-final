from django.urls import path

from .views import purchase_reward, rewards_page

urlpatterns = [
    path("", rewards_page, name="rewards"),
    path("purchase/<slug:slug>/", purchase_reward, name="purchase_reward"),
]

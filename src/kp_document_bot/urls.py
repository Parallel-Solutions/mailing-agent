from django.urls import path

from .views import (
    chat_view,
    generate_batch_view,
    generate_view,
    health_view,
    review_document_view,
    review_generated_view,
    review_text_view,
)


app_name = "kp_document_bot"


urlpatterns = [
    path("health/", health_view, name="health"),
    path("generate/", generate_view, name="generate"),
    path("generate-batch/", generate_batch_view, name="generate_batch"),
    path("review-generated/", review_generated_view, name="review_generated"),
    path("review-document/", review_document_view, name="review_document"),
    path("review-text/", review_text_view, name="review_text"),
    path("chat/", chat_view, name="chat"),
]

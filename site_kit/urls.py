from django.urls import path

from . import views

app_name = "site_kit"

urlpatterns = [
    path("", views.home_view, name="home"),
    path("seite/<slug:slug>/", views.page_detail_view, name="page_detail"),
    path("news/", views.news_list_view, name="news_list"),
    path("news/archiv/", views.news_archive_view, name="news_archive"),
    path("news/feed.xml", views.news_rss_view, name="news_rss"),
    path("news/<slug:slug>/", views.news_detail_view, name="news_detail"),
    path("termine/", views.events_list_view, name="events_list"),
    path("termine/<int:pk>/", views.event_detail_view, name="event_detail"),
    path("dozenten/", views.lecturers_list_view, name="lecturers_list"),
    path("personen/<int:pk>/", views.person_detail_view, name="person_detail"),
    path("programm/", views.annual_program_view, name="annual_program"),
    path("programm/pdf/", views.annual_program_pdf_view, name="annual_program_pdf"),
    path("programm/standard/", views.program_standard_view, name="program_standard"),
    path("programm/standard/pdf/", views.annual_program_pdf_view, name="program_standard_pdf"),
    path("programm/vorschau/<int:pk>/", views.offering_preview_view, name="offering_preview"),
    path("programm/<slug:program_slug>/pdf/", views.annual_program_pdf_view, name="program_detail_pdf"),
    path("programm/<slug:program_slug>/", views.annual_program_view, name="program_detail"),
    path("anmeldung/", views.event_registration_view, name="event_registration"),
    path("impressum/", views.imprint_view, name="imprint"),
    path("datenschutz/", views.privacy_view, name="privacy"),
    path("sitemap.xml", views.sitemap_view, name="sitemap"),
    path("robots.txt", views.robots_view, name="robots"),
    path("site/cockpit/", views.cockpit_view, name="cockpit"),
    path("site/manage/", views.manage_view, name="manage"),
    path("site/manage/programs/", views.program_manage_view, name="program_manage"),
    path("site/manage/programs/<int:pk>/blocks/", views.program_blocks_edit_view, name="program_blocks_edit"),
    path("site/manage/system/<slug:slug>/", views.system_page_edit_view, name="system_page_edit"),
    path("site/manage/pages/new/", views.page_create_view, name="page_create"),
    path("site/manage/pages/<int:pk>/", views.page_edit_view, name="page_edit"),
    path("site/manage/pages/<int:pk>/delete/", views.page_delete_view, name="page_delete"),
]

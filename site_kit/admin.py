from django.contrib import admin

from .models import PageBlock, PublicPage, SiteConfig


class PageBlockInline(admin.TabularInline):
    model = PageBlock
    extra = 0


@admin.register(SiteConfig)
class SiteConfigAdmin(admin.ModelAdmin):
    list_display = ("site_name", "contact_email", "member_app_url")


@admin.register(PublicPage)
class PublicPageAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "is_home", "is_published", "show_in_nav", "menu_order")
    list_filter = ("is_home", "is_published", "show_in_nav")
    inlines = [PageBlockInline]

from django.contrib import admin

from .models import QpsRecord


@admin.register(QpsRecord)
class QpsRecordAdmin(admin.ModelAdmin):
    list_display = ('ts', 'metric', 'value')
    list_filter = ('metric',)
    date_hierarchy = 'ts'

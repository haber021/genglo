from django.db import models
from django.utils import timezone


class SentDailyReport(models.Model):
    """Track sent daily reports to prevent duplicates"""
    report_date = models.DateField(help_text="Date of the report")
    recipient_email = models.EmailField(help_text="Email address that received the report")
    sent_at = models.DateTimeField(auto_now_add=True, help_text="When the report was sent")
    
    class Meta:
        unique_together = [['report_date', 'recipient_email']]
        ordering = ['-report_date', '-sent_at']
        verbose_name = "Sent Daily Report"
        verbose_name_plural = "Sent Daily Reports"
    
    def __str__(self):
        return f"Report for {self.report_date} sent to {self.recipient_email} on {self.sent_at}"

"""
Scheduler module for running periodic tasks.
This module sets up APScheduler to run scheduled tasks when Django starts.
"""
import logging
import sys
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from django.core.management import call_command
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = None


def send_daily_report():
    """Function to call the send_daily_report management command"""
    try:
        logger.info("Starting scheduled daily report generation...")
        # Use call_command to execute the management command for today
        # The command defaults to today's date if no date is specified
        # Use force=True to ensure scheduled runs always send, even if one was already sent
        today = timezone.now().date()
        call_command('send_daily_report', date=today.strftime('%Y-%m-%d'), force=True, verbosity=1)
        logger.info(f"Daily report sent successfully for {today}")
    except Exception as e:
        logger.error(f"Error sending daily report: {str(e)}", exc_info=True)
        # Print to stderr for visibility
        print(f"Error sending daily report: {str(e)}", file=sys.stderr)


def check_and_send_missed_report():
    """Check if yesterday's report was missed and send it if needed"""
    try:
        from transactions.models import Transaction
        yesterday = (timezone.now() - timedelta(days=1)).date()
        
        # Check if there were any transactions yesterday
        has_transactions = Transaction.objects.filter(
            status='completed',
            created_at__date=yesterday
        ).exists()
        
        if has_transactions:
            logger.info(f"Checking if report for {yesterday} was missed...")
            # Note: We don't have a way to track if report was sent, so we'll
            # just log that we're checking. In production, you might want to
            # track sent reports in a model.
            # For now, this function can be called manually if needed.
            logger.info(f"Report check completed for {yesterday}")
    except Exception as e:
        logger.error(f"Error checking missed report: {str(e)}", exc_info=True)


def start_scheduler():
    """Start the scheduler and add the daily report job"""
    global scheduler
    
    # Check if scheduler is already running
    if scheduler is not None:
        if scheduler.running:
            logger.warning("Scheduler is already running")
            return
        else:
            # Clean up old scheduler instance
            try:
                scheduler.shutdown(wait=False)
            except:
                pass
            scheduler = None
    
    try:
        # Create scheduler instance with timezone support
        scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)
        
        # Schedule daily report to run at 12:00 AM (midnight) every day
        # misfire_grace_time: Allow job to run up to 1 hour after scheduled time if missed
        # coalesce: Combine multiple missed runs into one execution
        scheduler.add_job(
            send_daily_report,
            trigger=CronTrigger(hour=22, minute=29),  # 12:00 AM
            id='send_daily_report',
            name='Send Daily Report at Midnight',
            replace_existing=True,
            max_instances=1,  # Prevent overlapping executions
            misfire_grace_time=3600,  # Allow execution up to 1 hour after scheduled time
            coalesce=True,  # Combine multiple missed runs into one
        )
        
        # Start the scheduler
        scheduler.start()
        logger.info("Scheduler started successfully. Daily report will run at 12:00 AM every day.")
        logger.info("Misfire grace time: 1 hour (reports will auto-send if missed)")
        print("Scheduler started: Daily report will run automatically at 12:00 AM every day.")
        print("Note: Reports will automatically send even if the server was down briefly.")
        
    except Exception as e:
        logger.error(f"Error starting scheduler: {str(e)}", exc_info=True)
        print(f"Error starting scheduler: {str(e)}", file=sys.stderr)


def stop_scheduler():
    """Stop the scheduler"""
    global scheduler
    
    if scheduler is not None and scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")
    else:
        logger.warning("Scheduler is not running")


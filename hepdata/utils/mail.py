from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from smtplib import SMTP

from celery import shared_task

from hepdata import config

@shared_task
def send_email(destination, subject, message):
    try:
        connection = connect()
        mmp_msg = MIMEMultipart('alternative')
        mmp_msg['Subject'] = subject
        mmp_msg['From'] = config.DEFAULT_SEND_EMAIL
        mmp_msg['To'] = destination

        part1 = MIMEText(message, 'html')
        mmp_msg.attach(part1)

        connection.sendmail(config.DEFAULT_SEND_EMAIL, destination, mmp_msg.as_string())
        connection.quit()
    except Exception as e:
        print 'Exception occurred.'
        raise e


def connect():
    smtp = SMTP()
    smtp.connect(config.SMTP_SERVER, config.SMTP_PORT)
    if not config.SMTP_NO_PASSWORD:
        smtp.login(config.DEFAULT_SEND_EMAIL, config.SMTP_PASSWORD)

    return smtp

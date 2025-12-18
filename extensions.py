from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from flask_apscheduler import APScheduler

db = SQLAlchemy()
mail = Mail()
login_manager = LoginManager()
scheduler = APScheduler()

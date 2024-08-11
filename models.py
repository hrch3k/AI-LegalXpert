from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from wtforms import  Form, StringField, DateField, SubmitField, SelectField, TextAreaField
from wtforms.validators import DataRequired
from flask_wtf import FlaskForm

db = SQLAlchemy()

class SavedAnalysis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    outcome = db.Column(db.String(100))  # e.g., 'won', 'lost', 'settled'
    duration = db.Column(db.Integer)  # Duration in days
    success_rate = db.Column(db.Float)  # Success rate as a percentage


class SearchForm(FlaskForm):
    query = StringField('Search', validators=[DataRequired()])
    start_date = DateField('Start Date', format='%Y-%m-%d', validators=[DataRequired()])
    end_date = DateField('End Date', format='%Y-%m-%d', validators=[DataRequired()])
    submit = SubmitField('Search')

class ReminderForm(Form):
    title = StringField('Title', validators=[DataRequired()])
    description = StringField('Description')
    due_date = DateField('Due Date', format='%Y-%m-%d', validators=[DataRequired()])
    submit = SubmitField('Add Reminder')

class Reminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    due_date = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    analysis_id = db.Column(db.Integer, db.ForeignKey('saved_analysis.id'))

class DocumentGenerationForm(Form):
    document_type = SelectField('Document Type', choices=[
        ('contract', 'Contract'),
        ('letter', 'Legal Letter'),
        ('memo', 'Legal Memorandum')
    ], validators=[DataRequired()])
    client_name = StringField('Client Name', validators=[DataRequired()])
    subject = StringField('Subject', validators=[DataRequired()])
    content = TextAreaField('Content', validators=[DataRequired()])
    submit = SubmitField('Generate Document')

from asyncio.log import logger
import os
import io
import traceback
from sqlalchemy import or_
import yaml
from flask import Flask, render_template, render_template_string, request, flash, redirect, url_for, session, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from collections import deque
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from models import DocumentGenerationForm, Reminder, ReminderForm, SavedAnalysis, SearchForm, db
import asyncio
from aijson import Flow
import nest_asyncio

# Apply nest_asyncio to allow reuse of the existing event loop
nest_asyncio.apply()

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)
migrate = Migrate(app, db)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///legal_xpert.db'

db.init_app(app)
recent_cases = deque(maxlen=5)
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_ai_config():
    with open('legal_case_analysis.ai.yaml', 'r') as file:
        return yaml.safe_load(file)

def read_file_content(file):
    file_extension = os.path.splitext(file.filename)[1].lower()
    if file_extension == '.txt':
        return file.read().decode('utf-8')
    elif file_extension in ['.doc', '.docx']:
        from docx import Document
        doc = Document(io.BytesIO(file.read()))
        return '\n'.join([para.text for para in doc.paragraphs])
    elif file_extension == '.pdf':
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(file.read()))
        return '\n'.join([page.extract_text() for page in reader.pages])
    return "Unsupported file format"

async def run_ai_flow(case_details, analysis_type):
    try:
        # Load the flow from the YAML file
        flow = Flow.from_file('legal_case_analysis.ai.yaml')
        
        # Set the variables in the flow
        flow = flow.set_vars(case_details=case_details, analysis_type=analysis_type)
        
        # Run the flow and return the result
        result = await flow.run()
        logger.info(f"AI flow raw result: {result}")
        
        # Check if the result is a dictionary and contains the 'analysis_result' key
        if isinstance(result, dict) and 'analysis_result' in result:
            analysis_result = result['analysis_result']
            logger.info(f"Extracted analysis result: {analysis_result}")
            return analysis_result
        else:
            logger.error(f"Unexpected result structure: {result}")
            return "Error: Unexpected result structure from AI analysis"
    except RuntimeError as e:
        if str(e) == "Failed to render result":
            logger.error("Failed to render result from AI flow. This may be due to an issue with the AI model or the input data.")
            return "Error: The AI model encountered an issue while processing your request. Please try again with different input or contact support."
        else:
            logger.error(f"Unexpected RuntimeError in AI flow: {str(e)}")
            return f"An unexpected error occurred: {str(e)}"
    except Exception as e:
        logger.error(f"Error running AI flow: {str(e)}")
        logger.error(traceback.format_exc())
        return f"Error in AI analysis: {str(e)}"

@app.route('/', methods=['GET', 'POST'])
async def index():
    result = None
    error = None
    if request.method == 'POST':
        try:
            analysis_type = request.form.get('analysis_type')
            case_details = request.form.get('case_details', '')

            # Process uploaded file if present
            if 'file' in request.files and request.files['file'].filename:
                file = request.files['file']
                if file and allowed_file(file.filename):
                    file_content = read_file_content(file)
                    case_details += f"\n\nUploaded File Content:\n{file_content}"
                else:
                    flash('Invalid file type. Please upload a txt, pdf, doc, or docx file.')
                    return render_template('index.html')
            
            if not case_details.strip():
                flash('Please provide case details either by pasting text or uploading a document.')
                return render_template('index.html')
            
            # Run the AI flow asynchronously using the await keyword
            result = await run_ai_flow(case_details, analysis_type)
            
            if result and not result.startswith("Error:"):
                logger.info(f"Analysis result: {result}")
                recent_cases.appendleft({
                    'analysis_type': analysis_type,
                    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'result': result
                })
                session['last_result'] = result
            else:
                error = result if result else "The AI analysis did not produce a valid result. Please try again."
                logger.warning(f"AI analysis issue: {error}")
        except Exception as e:
            error = f'An error occurred during the analysis: {str(e)}'
            logger.error(f"Error during AI analysis: {str(e)}", exc_info=True)
    
    search_form = SearchForm()
    saved_analyses = SavedAnalysis.query.order_by(SavedAnalysis.timestamp.desc()).limit(5).all()
    return render_template('index.html', result=result, error=error, recent_cases=list(recent_cases), saved_analyses=saved_analyses, search_form=search_form)

@app.route('/add_reminder/<int:analysis_id>', methods=['GET', 'POST'])
def add_reminder(analysis_id):
    form = ReminderForm()
    analysis = SavedAnalysis.query.get_or_404(analysis_id)
    
    if form.validate_on_submit():
        reminder = Reminder(
            title=form.title.data,
            description=form.description.data,
            due_date=form.due_date.data,
            analysis=analysis
        )
        db.session.add(reminder)
        db.session.commit()
        flash('Reminder added successfully!')
        return redirect(url_for('view_analysis', id=analysis_id))
    
    return render_template('add_reminder.html', form=form, analysis=analysis)

@app.route('/reminders')
def view_reminders():
    reminders = Reminder.query.order_by(Reminder.due_date).all()
    return render_template('reminders.html', reminders=reminders)


@app.route('/save_analysis', methods=['POST'])
def save_analysis():
    title = request.form.get('title', 'Untitled Analysis')
    content = session.get('last_result', 'No content available')
    new_analysis = SavedAnalysis(title=title, content=content)
    db.session.add(new_analysis)
    db.session.commit()
    flash('Analysis saved successfully!')
    return redirect(url_for('index'))

@app.route('/view_analysis/<int:id>')
def view_analysis(id):
    analysis = SavedAnalysis.query.get_or_404(id)
    reminders = Reminder.query.filter_by(analysis_id=id).order_by(Reminder.due_date).all()
    return render_template('view_analysis.html', analysis=analysis, reminders=reminders)

@app.route('/generate_document/<int:analysis_id>', methods=['GET', 'POST'])
def generate_document(analysis_id):
    form = DocumentGenerationForm()
    analysis = SavedAnalysis.query.get_or_404(analysis_id)
    
    if form.validate_on_submit():
        document_type = form.document_type.data
        client_name = form.client_name.data
        subject = form.subject.data
        content = form.content.data
        
        if document_type == 'contract':
            template = """
            <h1>Contract</h1>
            <p>This contract is between {{ client_name }} and Legal Xpert.</p>
            <h2>{{ subject }}</h2>
            <p>{{ content }}</p>
            <p>Signed: ____________________</p>
            <p>Date: {{ current_date }}</p>
            """
        elif document_type == 'letter':
            template = """
            <h1>Legal Letter</h1>
            <p>Dear {{ client_name }},</p>
            <p>Re: {{ subject }}</p>
            <p>{{ content }}</p>
            <p>Sincerely,</p>
            <p>Legal Xpert</p>
            """
        else:  # memo
            template = """
            <h1>Legal Memorandum</h1>
            <p>To: {{ client_name }}</p>
            <p>From: Legal Xpert</p>
            <p>Subject: {{ subject }}</p>
            <p>Date: {{ current_date }}</p>
            <hr>
            <p>{{ content }}</p>
            """
        
        document = render_template_string(
            template,
            client_name=client_name,
            subject=subject,
            content=content,
            current_date=datetime.now().strftime('%Y-%m-%d'))

@app.route('/export_result')
def export_result():
    result_text = session.get('last_result', 'No result available')
    buffer = io.StringIO()
    buffer.write(result_text)
    buffer.seek(0)
    return send_file(
        io.BytesIO(buffer.getvalue().encode()),
        mimetype='text/plain',
        as_attachment=True,
        download_name='legal_analysis_result.txt'
    )

@app.route('/search', methods=['GET', 'POST'])
def search():
    form = SearchForm()
    if form.validate_on_submit():
        query = form.query.data
        start_date = form.start_date.data
        end_date = form.end_date.data
        
        results = SavedAnalysis.query.filter(
            or_(
                SavedAnalysis.title.ilike(f'%{query}%'),
                SavedAnalysis.content.ilike(f'%{query}%')
            ),
            SavedAnalysis.timestamp.between(start_date, end_date)
        ).all()
        
        return render_template('search_results.html', results=results, form=form)
    
    return render_template('search.html', form=form)

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        db.create_all()
    app.run(debug=True)

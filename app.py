from asyncio.log import logger
import logging
import os
import io
import traceback
from bs4 import BeautifulSoup
from sqlalchemy import or_
import yaml
from flask import Flask, jsonify, render_template, render_template_string, request, flash, redirect, url_for, session, send_file
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
import re
from typing import List, Tuple
from PIL import Image

nest_asyncio.apply()

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)
migrate = Migrate(app, db)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///legal_xpert.db'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'tiff', 'txt', 'pdf', 'doc', 'docx'}

db.init_app(app)
recent_cases = deque(maxlen=5)
#ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def allowed_file(filename):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    is_allowed = ext in app.config['ALLOWED_EXTENSIONS']
    logger.debug(f"File extension: {ext}, Is allowed: {is_allowed}")
    return is_allowed

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

def calculate_case_metrics():
    total_cases = SavedAnalysis.query.count()
    won_cases = SavedAnalysis.query.filter_by(outcome='won').count()
    lost_cases = SavedAnalysis.query.filter_by(outcome='lost').count()
    
    average_duration = db.session.query(db.func.avg(SavedAnalysis.duration)).scalar()
    success_rate = (won_cases / total_cases) * 100 if total_cases > 0 else 0
    
    return {
        "total_cases": total_cases,
        "won_cases": won_cases,
        "lost_cases": lost_cases,
        "average_duration": average_duration,
        "success_rate": success_rate
    }

def clean_ai_response(response: str) -> str:
    cleaned_response = BeautifulSoup(response, "html.parser").get_text()
    cleaned_response = re.sub(r'\s+', ' ', cleaned_response).strip()
    cleaned_response = re.sub(r'([.!?]){2,}', r'\1', cleaned_response)
    cleaned_response = re.sub(r'(?<=[.,!?])(?=[^\s])', r' ', cleaned_response)
    if cleaned_response:
        cleaned_response = cleaned_response[0].upper() + cleaned_response[1:]
    return cleaned_response

def format_ai_response(response: str) -> str:
    cleaned_response = clean_ai_response(response)
    paragraphs = re.split(r'\n+', cleaned_response)
    formatted_paragraphs = [format_paragraph(p) for p in paragraphs]
    return '\n\n'.join(formatted_paragraphs)

def format_paragraph(paragraph: str) -> str:
    paragraph = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', paragraph)
    paragraph = re.sub(r'\*(.*?)\*', r'<em>\1</em>', paragraph)
    key_phrases = [
        (r'\b(Note|Important|Tip):', r'<strong>\1:</strong>'),
        (r'\b(e\.g\.|i\.e\.):', r'<em>\1</em>'),
    ]
    for pattern, replacement in key_phrases:
        paragraph = re.sub(pattern, replacement, paragraph)
    paragraph = re.sub(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b', r'<code>\1</code>', paragraph)
    return f'<p>{paragraph}</p>'

def structure_response(response: str) -> str:
    formatted_response = format_ai_response(response)
    separator = '<hr style="border: 1px solid #ccc; margin: 20px 0;">'
    styled_response = f'''
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; background-color: #f9f9f9; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
        {formatted_response}
        {separator}
        <footer style="font-size: 0.9em; color: #777; text-align: center;">
            Generated by AI Assistant
        </footer>
    </div>
    '''
    return styled_response

async def run_ai_flow(case_details, analysis_type):
    try:
        flow = Flow.from_file('legal_case_analysis.ai.yaml')
        flow = flow.set_vars(case_details=case_details, analysis_type=analysis_type)
        result = await flow.run()
        logger.info(f"AI flow raw result: {result}")
        
        if isinstance(result, dict):
            if 'analysis_result' in result:
                analysis_result = result['analysis_result']
            else:
                analysis_result = str(result)
        else:
            analysis_result = str(result)
        
        cleaned_result = clean_ai_response(analysis_result)
        return {"analysis_result": cleaned_result}
    
    except RuntimeError as e:
        error_message = "The AI model encountered an issue while processing your request. Please try again with different input or contact support."
        if str(e) == "Failed to render result":
            logger.error("Failed to render result from AI flow. This may be due to an issue with the AI model or the input data.")
        else:
            logger.error(f"Unexpected RuntimeError in AI flow: {str(e)}")
        return {"analysis_result": error_message}
    except Exception as e:
        logger.error(f"Error running AI flow: {str(e)}")
        logger.error(traceback.format_exc())
        return {"analysis_result": f"Error in AI analysis: {str(e)}"}
    
    
async def analyze_image(image_file):
    try:
        # Your flow setup and execution
        flow = Flow.from_file('image_analysis.ai.yaml')
        flow = flow.set_vars(image_file=image_file)
        result = await flow.run(target_output='forensic_image_analysis.result')
        
        if result is None:
            raise ValueError("No result returned from the AI flow.")
        
        return {"analysis_result": result}
    
    except Exception as e:
        logging.error(f"Error running AI flow: {str(e)}")
        return {"analysis_result": f"Error in AI analysis: {str(e)}"}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/analyze_image', methods=['POST'])
async def analyze_image_route():
    if 'image' not in request.files:
        flash('No image file provided.')
        return redirect(url_for('index'))

    file = request.files['image']
    if file.filename == '':
        flash('No selected file.')
        return redirect(url_for('index'))

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        try:
            analysis_result = await analyze_image(file_path)
            cleaned_result = clean_ai_response(analysis_result['analysis_result'])
            formatted_result = structure_response(cleaned_result)
            return render_template('image_analysis_result.html', result=formatted_result)
        except Exception as e:
            flash(f'An error occurred while analyzing the image: {str(e)}')
            return redirect(url_for('index'))
    else:
        flash('Invalid file type. Please upload a valid image file.')
        return redirect(url_for('index'))


from xhtml2pdf import pisa
@app.route('/generate_report', methods=['POST'])
def generate_report():
    report_title = "Case Report"
    client_name = request.form.get('client_name')
    summary = request.form.get('summary')
    details = request.form.get('details')

    
    html_content = render_template(
        'report_template.html',
        report_title=report_title,
        client_name=client_name,
        report_date=datetime.now().strftime('%Y-%m-%d'),
        summary=summary,
        details=details
    )

    
    pdf = io.BytesIO()
    pisa_status = pisa.CreatePDF(io.StringIO(html_content), dest=pdf)

    if pisa_status.err:
        
        return "Error generating PDF", 500

    pdf.seek(0) 

    
    return send_file(
        pdf,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'{report_title}.pdf'
    )
@app.route('/api/case_metrics')
def case_metrics_api():
    metrics = calculate_case_metrics()
    return jsonify(metrics)

@app.route('/dashboard')
def dashboard():
    metrics = calculate_case_metrics()
    return render_template('view_analysis.html', metrics=metrics)

@app.route('/', methods=['GET', 'POST'])
async def index():
    result = None
    error = None
    if request.method == 'POST':
        try:
            analysis_type = request.form.get('analysis_type')
            case_details = request.form.get('case_details', '')

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
            
            ai_result = await run_ai_flow(case_details, analysis_type)
            
            if 'analysis_result' in ai_result:
                result = ai_result['analysis_result']
                session['last_result'] = result
                result = structure_response(result)
                if not result.startswith("Error:"):
                    logger.info(f"Analysis result: {result}")
                    recent_cases.appendleft({
                        'analysis_type': analysis_type,
                        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'result': result
                    })
                else:
                    error = result
                    logger.warning(f"AI analysis issue: {error}")
            else:
                error = "The AI analysis did not produce a valid result. Please try again."
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
        else:
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
    formatted_result = structure_response(result_text)
    return send_file(
        io.BytesIO(formatted_result.encode()),
        mimetype='text/html',
        as_attachment=True,
        download_name='legal_analysis_result.html'
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

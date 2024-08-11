import os
import io
import json
import yaml
import boto3
from flask import Flask, render_template, request, flash, redirect, url_for, session, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from collections import deque
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from models import SavedAnalysis, db

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
    with open('chatbot.ai.yaml', 'r') as file:
        return yaml.safe_load(file)

def get_bedrock_runtime():
    return boto3.client(
        service_name='bedrock-runtime',
        region_name=os.getenv('AWS_REGION_NAME'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY')
    )

def invoke_model(client, model_id, prompt):
    body = json.dumps({
        "prompt": prompt,
        "temperature": 0.7,
        "top_p": 1,
        "max_gen_len": 2000
    })
    
    response = client.invoke_model(
        body=body,
        modelId=model_id,
        accept='application/json',
        contentType='application/json'
    )
    
    response_body = json.loads(response.get('body').read())
    return response_body.get('generation').replace('*', '')

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

@app.route('/', methods=['GET', 'POST'])
def index():
    result = None
    if request.method == 'POST':
        config = load_ai_config()
        analysis_type = request.form.get('analysis_type')
        client = get_bedrock_runtime()
        model_id = config['model']
        
        # Get prompts for the general_analysis method
        method_prompts = config['methods']['general_analysis']['prompts']
        system_prompt = next(prompt['content'] for prompt in method_prompts if prompt['role'] == 'system')
        user_prompt = next(prompt['content'] for prompt in method_prompts if prompt['role'] == 'user')
        
        case_details = request.form.get('case_details', '')

        # Process uploaded file if present
        if 'file' in request.files and request.files['file'].filename:
            file = request.files['file']
            if file and allowed_file(file.filename):
                file_content = read_file_content(file)
                case_details += f"\n\nUploaded File Content:\n{file_content}"
                
                file_type = file.filename.rsplit('.', 1)[1].lower()
                file_type_prompt = config['file_type_prompts'].get(file_type, '')
                if file_type_prompt:
                    case_details = f"{file_type_prompt}\n\n{case_details}"
            else:
                flash('Invalid file type. Please upload a txt, pdf, doc, or docx file.')
                return render_template('index.html')
        
        if not case_details.strip():
            flash('Please provide case details either by pasting text or uploading a document.')
            return render_template('index.html')
        
        full_prompt = f"{system_prompt}\n\nHuman: {user_prompt.format(case_details=case_details, analysis_type=analysis_type)}\n\nAssistant:"
        result = invoke_model(client, model_id, full_prompt)
        
        if result:
            recent_cases.appendleft({
                'analysis_type': analysis_type,
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
            session['last_result'] = result
    
    saved_analyses = SavedAnalysis.query.order_by(SavedAnalysis.timestamp.desc()).limit(5).all()
    return render_template('index.html', result=result, recent_cases=list(recent_cases), saved_analyses=saved_analyses)

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
    return render_template('view_analysis.html', analysis=analysis)

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

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    with app.app_context():
        db.create_all()
    app.run(debug=True)
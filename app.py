from flask import Flask, render_template, request, flash
import os
from dotenv import load_dotenv
import yaml
import boto3
import json
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

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
    ai_response = response_body.get('generation')
    
    cleaned_response = ai_response.replace('*', '')
    
    return cleaned_response

def read_file_content(file_path):
    _, file_extension = os.path.splitext(file_path)
    
    if file_extension.lower() == '.txt':
        with open(file_path, 'r') as file:
            return file.read()
    elif file_extension.lower() in ['.doc', '.docx']:
        # You'll need to install python-docx: pip install python-docx
        from docx import Document
        doc = Document(file_path)
        return '\n'.join([paragraph.text for paragraph in doc.paragraphs])
    elif file_extension.lower() == '.pdf':
        # You'll need to install PyPDF2: pip install PyPDF2
        from PyPDF2 import PdfReader
        reader = PdfReader(file_path)
        return '\n'.join([page.extract_text() for page in reader.pages])
    else:
        return "Unsupported file format"

@app.route('/', methods=['GET', 'POST'])
def index():
    result = None
    if request.method == 'POST':
        config = load_ai_config()
        analysis_type = request.form['analysis_type']
        
        client = get_bedrock_runtime()
        model_id = config['model']
        
        system_prompt = next(prompt['content'] for prompt in config['prompts'] if prompt['role'] == 'system')
        user_prompt = next(prompt['content'] for prompt in config['prompts'] if prompt['role'] == 'user')
        
        if 'file' in request.files:
            file = request.files['file']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                
                file_content = read_file_content(file_path)
                case_details = file_content
            else:
                flash('Invalid file type. Please upload a txt, pdf, doc, or docx file.')
                return render_template('index.html')
        else:
            case_details = request.form['case_details']
        
        full_prompt = f"{system_prompt}\n\nHuman: {user_prompt.format(case_details=case_details, analysis_type=analysis_type)}\n\nAssistant:"
        
        result = invoke_model(client, model_id, full_prompt)
    
    return render_template('index.html', result=result)

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.run(debug=True)
from flask import Flask, render_template, request
import os
from dotenv import load_dotenv
import yaml
import boto3
import json

load_dotenv()

app = Flask(__name__)

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
        "max_gen_len": 2000  # This replaces max_tokens_to_sample
    })
    
    response = client.invoke_model(
        body=body,
        modelId=model_id,
        accept='application/json',
        contentType='application/json'
    )
    
    response_body = json.loads(response.get('body').read())
    ai_response = response_body.get('generation')  # Changed from 'completion' to 'generation'
    
    # Clean up the response to remove asterisks
    cleaned_response = ai_response.replace('*', '')
    
    return cleaned_response

@app.route('/', methods=['GET', 'POST'])
def index():
    result = None
    if request.method == 'POST':
        config = load_ai_config()
        case_details = request.form['case_details']
        analysis_type = request.form['analysis_type']
        
        client = get_bedrock_runtime()
        model_id = config['model']
        
        system_prompt = next(prompt['content'] for prompt in config['prompts'] if prompt['role'] == 'system')
        user_prompt = next(prompt['content'] for prompt in config['prompts'] if prompt['role'] == 'user')
        
        full_prompt = f"{system_prompt}\n\nHuman: {user_prompt.format(case_details=case_details, analysis_type=analysis_type)}\n\nAssistant:"
        
        result = invoke_model(client, model_id, full_prompt)
    
    return render_template('index.html', result=result)

if __name__ == '__main__':
    app.run(debug=True)

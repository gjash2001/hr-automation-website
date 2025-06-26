import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, Response
from werkzeug.utils import secure_filename
import docx
import re
import pdfplumber
from datetime import datetime
import dateparser
from dateutil.relativedelta import relativedelta   # <-- Add this line


app = Flask(__name__)

UPLOAD_FOLDER = 'uploaded_resumes'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def extract_skills(text):
    COMMON_SKILLS = [
        "python", "java", "c++", "azure", "aws", "docker", "kubernetes", "sql", "pandas", "power bi", "databricks",
        "data factory", "snowflake", "devops", "html", "css", "javascript", "git", "linux", "adf", "etl", "spark"
        # add more as needed!
    ]
    found = set()
    text_lower = text.lower()
    for skill in COMMON_SKILLS:
        # Simple check if skill appears in resume text
        if skill in text_lower:
            found.add(skill.title())
    return '; '.join(sorted(found))

def extract_experience(text):
    experience = ''
    lines = text.lower().split('\n')
    exp_keywords = ['experience', 'work experience', 'professional experience']
    start_idx = -1
    for idx, line in enumerate(lines):
        if any(keyword in line for keyword in exp_keywords):
            start_idx = idx
            break
    if start_idx != -1:
        exp_lines = []
        for l in lines[start_idx+1:start_idx+6]:  # up to 5 lines after header
            if l.strip() and not any(keyword in l for keyword in exp_keywords):
                exp_lines.append(l.strip())
        experience = '; '.join(exp_lines)
    return experience

def extract_age(text):
    dob_match = re.search(r'(?:dob|date of birth)[:\s]*([0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4})', text.lower())
    age = ''
    if dob_match:
        dob_str = dob_match.group(1)
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                dob = datetime.strptime(dob_str, fmt)
                today = datetime.today()
                age = str(today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)))
                break
            except:
                continue
    return age

def parse_resume(filepath):
    candidate_name_guess = os.path.splitext(os.path.basename(filepath))[0]
    age = ''
    email = ''
    phone = ''
    skills = ''
    text = ''
    if filepath.endswith('.pdf'):
        try:
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + '\n'
        except Exception as e:
            print(f"PDF extract error: {e}")
    elif filepath.endswith('.docx'):
        try:
            doc = docx.Document(filepath)
            text = '\n'.join([para.text for para in doc.paragraphs])
        except Exception as e:
            print(f"DOCX extract error: {e}")
    if text:
        lines = text.strip().split('\n')
        for line in lines:
            if line.strip():
                candidate_name_guess = line.strip()
                break
    # Find email
    match = re.search(r'[\w\.-]+@[\w\.-]+', text)
    email = match.group(0) if match else ''
    # Find phone
    match = re.search(r'(\+?\d{1,3}[\s-]?)?\(?\d{3,5}\)?[\s-]?\d{3,5}[\s-]?\d{3,5}', text)
    phone = match.group(0) if match else ''
    skills = extract_skills(text)
    age = extract_age(text)
    experience = extract_experience_details(text)   # <-- Only this!
    return candidate_name_guess, age, email, phone, experience, skills


def extract_experience_details(text):
    experience_list = []
    lines = text.split('\n')
    roles = []
    # Find blocks starting with "Role: " and "Client: "
    for idx, line in enumerate(lines):
        line = line.strip()
        if line.lower().startswith('role:'):
            role = line[5:].strip()
            client = ''
            duration = ''
            # Next lines: find Client and Duration
            for j in range(idx+1, min(idx+5, len(lines))):
                if lines[j].strip().lower().startswith('client:'):
                    client = lines[j].split(':', 1)[1].strip().split('(')[0]
                # Look for date range in this line or next line
                date_match = re.search(r'([A-Za-z]+\s?\d{4}|[A-Za-z]+\s?\d{1,4}|[0-9]{4})\s*(to|-|–|—|until|till|present|now|current)\s*([A-Za-z]+\s?\d{4}|[A-Za-z]+\s?\d{1,4}|[0-9]{4}|now|present|current)', lines[j], re.I)
                if date_match:
                    start = date_match.group(1)
                    end = date_match.group(3)
                    # Try to parse to dates
                    start_date = dateparser.parse(start)
                    end_date = dateparser.parse(end) if end.lower() not in ['now','present','current'] else datetime.now()
                    if start_date and end_date:
                        delta = relativedelta(end_date, start_date)
                        years = delta.years
                        months = delta.months
                        years_str = f"{years} years" if years else ""
                        months_str = f"{months} months" if months else ""
                        duration = (years_str + " " + months_str).strip()
                    else:
                        duration = f"{start} - {end}"
            if role and client and duration:
                roles.append(f"{role} at {client} ({duration})")
    return '; '.join(roles)


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/candidates')
def candidates():
    conn = sqlite3.connect('hr.db')
    c = conn.cursor()
    c.execute('SELECT * FROM candidate')
    candidates = c.fetchall()
    conn.close()
    return render_template('candidates.html', candidates=candidates)

@app.route('/download_resume/<filename>')
def download_resume(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/upload_resumes', methods=['GET', 'POST'])
def upload_resumes():
    if request.method == 'POST':
        files = request.files.getlist('resumes')
        for file in files:
            if file.filename.lower().endswith(('.pdf', '.docx')):
                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)
                name, age, email, phone, experience, skills = parse_resume(filepath)
                conn = sqlite3.connect('hr.db')
                c = conn.cursor()

                # --- Duplicate check and delete logic HERE! ---
                c.execute('SELECT id, resume_file FROM candidate WHERE email=? OR phone=?', (email, phone))
                existing = c.fetchone()
                if existing:
                    old_id, old_file = existing
                    try:
                        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], old_file))
                    except Exception as e:
                        print("Error deleting old file:", e)
                    c.execute('DELETE FROM candidate WHERE id=?', (old_id,))

                # --- Insert new record ---
                c.execute('INSERT INTO candidate (name, age, email, phone, experience, skills, resume_file) VALUES (?, ?, ?, ?, ?, ?, ?)',
                          (name, age, email, phone, experience, skills, filename))
                conn.commit()
                conn.close()
        return redirect(url_for('candidates'))
    return render_template('upload_resumes.html')



@app.route('/export_candidates')
def export_candidates():
    conn = sqlite3.connect('hr.db')
    c = conn.cursor()
    c.execute('SELECT * FROM candidate')
    candidates = c.fetchall()
    conn.close()
    def generate():
        data = [['ID', 'Name', 'Age', 'Email', 'Phone', 'Experience', 'Skills', 'Resume']]
        data += candidates
        for row in data:
            yield ','.join(str(x) for x in row) + '\n'
    return Response(generate(), mimetype='text/csv', headers={"Content-Disposition": "attachment;filename=candidates.csv"})

if __name__ == '__main__':
    app.run(debug=True)

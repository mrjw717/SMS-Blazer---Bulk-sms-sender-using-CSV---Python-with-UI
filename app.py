from flask import Flask, render_template, request, redirect, url_for
from ppadb.client import Client as AdbClient
import time
import csv
from datetime import datetime, timedelta
import threading
import os
import uuid
import json

app = Flask(__name__)
UPLOAD_FOLDER = 'csv_uploads'
CAMPAIGN_FOLDER = 'campaigns'
LOG_FILE = 'sms_log.json'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['CAMPAIGN_FOLDER'] = CAMPAIGN_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
if not os.path.exists(CAMPAIGN_FOLDER):
    os.makedirs(CAMPAIGN_FOLDER)

def get_connected_devices():
    try:
        client = AdbClient(host="127.0.0.1", port=5037)
        devices = client.devices()
        return [device.serial for device in devices]
    except Exception as e:
        print(f"Error getting devices: {e}")
        return []

def send_sms(phone_number, message, device_serial, log, x_coord, y_coord):
    try:
        client = AdbClient(host="127.0.0.1", port=5037)
        device = client.device(device_serial)
        device.shell(f"am start -a android.intent.action.SENDTO -d sms:{phone_number} --es sms_body \"{message}\"")
        time.sleep(1)
        device.shell(f"input tap {x_coord} {y_coord}")
        log['status'] = 'sent'
    except Exception as e:
        log['status'] = f'failed: {e}'
        raise Exception(f"Error sending SMS: {e}")
    finally:
        log['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        write_log(log)

sending_thread = None
stop_sending = False

def send_bulk_sms(device_serial, csv_data, delay, log_entries, campaign_folder, campaign_id, x_coord, y_coord):
    global sending_thread, stop_sending
    sent_count = 0
    
    for row in csv_data:
        if stop_sending:
            break
        phone_number = row.get('number')
        message = row.get('message')
        
        if not phone_number or not message:
            print(f"Skipping row due to missing data: {row}")
            continue
        
        log = {'number': phone_number, 'message': message, 'status': 'pending'}
        log_entries.append(log)
        try:
            send_sms(phone_number, message, device_serial, log, x_coord, y_coord)
            sent_count += 1
            time.sleep(delay)
        except Exception as e:
            print(f"Error sending SMS to {phone_number}: {e}")
    stop_sending = False
    sending_thread = None
    
    campaign_data = {
        'csv_data': csv_data,
        'delay': delay,
        'log_entries': log_entries,
        'time_submitted': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    os.makedirs(campaign_folder, exist_ok=True)
    with open(os.path.join(campaign_folder, f'{campaign_id}.json'), 'w') as f:
        json.dump(campaign_data, f, indent=4)

def get_csv_files():
    files = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) if f.endswith('.csv')]
    return files

def read_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []

def write_log(log_entry):
    log_entries = read_log()
    log_entries.append(log_entry)
    with open(LOG_FILE, 'w') as f:
        json.dump(log_entries, f, indent=4)

def clear_log():
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)

def get_campaign_files():
    campaigns = []
    for folder in os.listdir(app.config['CAMPAIGN_FOLDER']):
        if os.path.isdir(os.path.join(app.config['CAMPAIGN_FOLDER'], folder)):
            for file in os.listdir(os.path.join(app.config['CAMPAIGN_FOLDER'], folder)):
                if file.endswith('.json'):
                    campaigns.append((folder, file))
    campaigns.sort(key=lambda x: x[0], reverse=True)
    return campaigns

@app.route('/', methods=['GET', 'POST'])
def index():
    global sending_thread, stop_sending
    devices = get_connected_devices()
    csv_files = get_csv_files()
    campaign_files = get_campaign_files()
    csv_data = []
    selected_csv_file = None
    error_message = None
    log_entries = read_log()
    message_sent = False
    campaign_data = None
    estimated_time = None
    if request.method == 'POST':
        if 'upload_csv' in request.form:
            if 'csv_file' in request.files:
                csv_file = request.files['csv_file']
                if csv_file.filename != '':
                    file_extension = os.path.splitext(csv_file.filename)[1]
                    unique_filename = str(uuid.uuid4()) + file_extension
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                    csv_file.save(file_path)
                    return render_template('index.html', devices=devices, csv_files=csv_files, message_sent=False, error=error_message, log_entries=log_entries, campaign_files=campaign_files, campaign_data=campaign_data, estimated_time=estimated_time)
        elif 'load_csv' in request.form:
            selected_csv_file = request.form.get('csv_file_select')
            if selected_csv_file:
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], selected_csv_file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        csv_data = list(reader)
                        print(f"CSV Data: {csv_data}")
                except Exception as e:
                    error_message = f"Error reading CSV file: {e}"
                    print(error_message)
                    csv_data = []
                
                campaign_id = os.path.splitext(selected_csv_file)[0]
                campaign_folder = os.path.join(app.config['CAMPAIGN_FOLDER'], campaign_id)
                campaign_file = os.path.join(campaign_folder, f'{campaign_id}.json')
                if os.path.exists(campaign_file):
                    with open(campaign_file, 'r') as f:
                        campaign_data = json.load(f)
                
                return render_template('index.html', devices=devices, csv_files=csv_files, csv_data=csv_data, selected_csv_file=selected_csv_file, error=error_message, log_entries=log_entries, campaign_files=campaign_files, campaign_data=campaign_data, estimated_time=estimated_time)
        elif 'send_sms' in request.form:
            if sending_thread and sending_thread.is_alive():
                return render_template('index.html', devices=devices, csv_files=csv_files, csv_data=csv_data, selected_csv_file=selected_csv_file, error=error_message, log_entries=log_entries, campaign_files=campaign_files, campaign_data=campaign_data, estimated_time=estimated_time)
            device_serial = request.form.get('device_serial')
            delay = int(request.form.get('delay', 1)) if request.form.get('delay') else 1
            selected_csv_file = request.form.get('csv_file_select')
            x_coord = request.form.get('x_coord', '666')
            y_coord = request.form.get('y_coord', '1409')
            
            if selected_csv_file:
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], selected_csv_file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        csv_data = list(reader)
                    
                    campaign_id = os.path.splitext(selected_csv_file)[0]
                    campaign_folder = os.path.join(app.config['CAMPAIGN_FOLDER'], campaign_id)
                    sending_thread = threading.Thread(target=send_bulk_sms, args=(device_serial, csv_data, delay, log_entries, campaign_folder, campaign_id, x_coord, y_coord))
                    sending_thread.start()
                    
                    return redirect(url_for('index', message_sent=True))
                except Exception as e:
                    error_message = f"Error reading CSV file: {e}"
                    print(error_message)
                    csv_data = []
        elif 'stop_sending' in request.form:
            stop_sending = True
            
        elif 'clear_log' in request.form:
            clear_log()
            log_entries = []
            return render_template('index.html', devices=devices, csv_files=csv_files, csv_data=csv_data, selected_csv_file=selected_csv_file, error=error_message, log_entries=log_entries, campaign_files=campaign_files, campaign_data=campaign_data, estimated_time=estimated_time)
    
    if csv_data:
        delay = int(request.form.get('delay', 1)) if request.form.get('delay') else 1
        estimated_seconds = len(csv_data) * delay
        if estimated_seconds < 60:
            estimated_time = f"it will take about {estimated_seconds} seconds to complete."
        else:
            estimated_minutes = estimated_seconds // 60
            estimated_time = f"it will take about {estimated_minutes} minutes to complete."
    
    return render_template('index.html', devices=devices, csv_files=csv_files, csv_data=csv_data, selected_csv_file=selected_csv_file, error=error_message, log_entries=log_entries, campaign_files=campaign_files, campaign_data=campaign_data, estimated_time=estimated_time)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

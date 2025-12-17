"""
Code with Destiny - Book Purchase Backend
Production-level Flask API for Razorpay payment processing
"""

import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
import razorpay
import os
import json
import hashlib
import hmac
from dotenv import load_dotenv
import requests
from datetime import datetime
import time
import sqlite3
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# ✅ CORS Configuration - Fixed with credentials support
CORS(app,
    resources={r"/api/*": {
        "origins": [
            "https://destinycode4u.vercel.app",
            "http://localhost:3000",
            "http://localhost:5000",
            "http://localhost:3002",
            "https://piyush-joshi1.github.io"
        ],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
        "supports_credentials": True,
        "max_age": 86400,
        "send_wildcard": False
    }},
    expose_headers=["Content-Type"],
    intercept_exceptions=False
)

# ✅ Add manual CORS headers for all responses (prevent duplicates)
@app.after_request
def after_request(response):
    origin = request.headers.get('Origin')
    allowed_origins = [
        "https://destinycode4u.vercel.app",
        "http://localhost:3000",
        "http://localhost:5000",
        "http://localhost:3002",
        "https://piyush-joshi1.github.io"
    ]
    
    if origin in allowed_origins:
        # Only add headers if they don't already exist (prevent duplicates)
        if 'Access-Control-Allow-Origin' not in response.headers:
            response.headers.add('Access-Control-Allow-Origin', origin)
        if 'Access-Control-Allow-Credentials' not in response.headers:
            response.headers.add('Access-Control-Allow-Credentials', 'true')
        if 'Access-Control-Allow-Headers' not in response.headers:
            response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
        if 'Access-Control-Allow-Methods' not in response.headers:
            response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
    
    # Set Content-Type for all responses
    if 'Content-Type' not in response.headers:
        response.headers.add('Content-Type', 'application/json')
    
    return response

# ✅ Handle preflight requests (OPTIONS) BEFORE other handlers
@app.before_request
def handle_preflight():
    if request.method == 'OPTIONS':
        print(f'📋 OPTIONS preflight request to {request.path}')
        response = jsonify({'status': 'ok'})
        return response, 200

# Add request logging (skip OPTIONS)
@app.before_request
def log_request():
    if request.method != 'OPTIONS':
        print(f'📨 {request.method} {request.path} from {request.remote_addr}')

# Initialize Razorpay client
razorpay_client = razorpay.Client(
    auth=(os.getenv('RAZORPAY_KEY_ID'), os.getenv('RAZORPAY_KEY_SECRET'))
)

# Database setup
DATABASE = 'orders.db'

def init_db():
    """Initialize SQLite database"""
    if not os.path.exists(DATABASE):
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        
        # Create orders table
        c.execute('''
            CREATE TABLE orders (
                id TEXT PRIMARY KEY,
                user_name TEXT NOT NULL,
                user_email TEXT NOT NULL,
                user_whatsapp TEXT NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL,
                payment_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create payments table
        c.execute('''
            CREATE TABLE payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                razorpay_payment_id TEXT UNIQUE,
                razorpay_order_id TEXT,
                razorpay_signature TEXT,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
        ''')
        
        conn.commit()
        conn.close()
        print('✅ Database initialized')

# Initialize database on app start
init_db()

# ==================== Database Helper Functions ====================

def insert_order(order_id, name, email, whatsapp, amount):
    """Insert a new order"""
    try:
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute('''
            INSERT INTO orders (id, user_name, user_email, user_whatsapp, amount, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (order_id, name, email, whatsapp, amount, 'created'))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f'❌ Database error: {e}')
        return False

def update_order_payment(order_id, payment_id, status):
    """Update order with payment information"""
    try:
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute('''
            UPDATE orders SET payment_id = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (payment_id, status, order_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f'❌ Database error: {e}')
        return False

def get_order(order_id):
    """Get order details"""
    try:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM orders WHERE id = ?', (order_id,))
        order = c.fetchone()
        conn.close()
        return dict(order) if order else None
    except Exception as e:
        print(f'❌ Database error: {e}')
        return None

# ==================== Email Functions ====================

def send_email(recipient_email, subject, message, drive_link=None):
    """Send email to user with Google Drive link"""
    try:
        sender_email = os.getenv('SMTP_EMAIL')
        sender_password = os.getenv('SMTP_PASSWORD')
        
        # If no credentials, just log it
        if not sender_email or not sender_password:
            print(f'📧 Email would be sent to: {recipient_email}')
            print(f'Subject: {subject}')
            print(f'Message: {message}')
            if drive_link:
                print(f'📥 Download Link: {drive_link}')
            return True
        
        # Create email message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = sender_email
        msg['To'] = recipient_email
        
        # Create HTML email body
        if drive_link:
            html_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                        <h2 style="color: #8B4513;">🎉 Your Book is Ready!</h2>
                        <p>Dear Customer,</p>
                        <p>Thank you for purchasing <strong>Code with Destiny</strong>!</p>
                        <p>Your book is now ready to download from the link below:</p>
                        
                        <div style="background-color: #f0f0f0; padding: 15px; border-radius: 5px; margin: 20px 0;">
                            <p><strong>📥 Download Your Book:</strong></p>
                            <a href="{drive_link}" style="background-color: #8B4513; color: white; padding: 12px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">Click Here to Download</a>
                        </div>
                        
                        <p>Or copy this link directly:</p>
                        <p style="background-color: #f9f9f9; padding: 10px; border-left: 4px solid #8B4513; word-break: break-all;">{drive_link}</p>
                        
                        <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                        
                        <p><strong>Order Details:</strong></p>
                        <p>{message}</p>
                        
                        <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                        
                        <p style="color: #666; font-size: 12px;">Best regards,<br><strong>Code with Destiny Team</strong></p>
                        <p style="color: #999; font-size: 11px;">© 2025 Code with Destiny. All rights reserved.</p>
                    </div>
                </body>
            </html>
            """
        else:
            html_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif; color: #333;">
                    <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                        <h2 style="color: #8B4513;">✅ Free Access Confirmed!</h2>
                        <p>Dear Customer,</p>
                        {message}
                        <p style="color: #666; font-size: 12px;">Best regards,<br><strong>Code with Destiny Team</strong></p>
                    </div>
                </body>
            </html>
            """
        
        part = MIMEText(html_body, 'html')
        msg.attach(part)
        
        # Send email via Gmail SMTP
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        
        print(f'✅ Email sent successfully to: {recipient_email}')
        if drive_link:
            print(f'📥 Download link shared: {drive_link}')
        return True
        
    except Exception as e:
        print(f'❌ Email error: {e}')
        return False

def send_email_async(recipient_email, subject, message, drive_link=None):
    """Send email in background thread (non-blocking)"""
    def _send():
        try:
            send_email(recipient_email, subject, message, drive_link)
        except Exception as e:
            print(f'❌ Async email error: {e}')
    
    thread = threading.Thread(target=_send, daemon=True)
    thread.start()

# ==================== API Routes ====================

@app.route('/')
def home():
    """Health check endpoint"""
    return jsonify({
        'status': 'success',
        'message': 'Code with Destiny Backend API',
        'version': '1.0.0'
    })

@app.route('/api/health', methods=['GET'])
def health_check():
    """API health check"""
    return jsonify({
        'status': 'success',
        'message': 'API is running',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/orders/create', methods=['POST', 'OPTIONS'])
def create_order():
    """
    Create a new Razorpay order
    
    Request body:
    {
        "name": "User Name",
        "email": "user@email.com",
        "whatsapp": "+91xxxxxxxxxx",
        "amount": 99
    }
    """
    try:
        # Handle preflight
        if request.method == 'OPTIONS':
            return '', 200
        
        # Ensure we have JSON data
        if not request.is_json:
            return jsonify({
                'status': 'error',
                'message': 'Content-Type must be application/json'
            }), 415
        
        data = request.get_json()
        
        # Validate input
        required_fields = ['name', 'email', 'whatsapp', 'amount']
        if not all(field in data for field in required_fields):
            return jsonify({
                'status': 'error',
                'message': 'Missing required fields: name, email, whatsapp, amount'
            }), 400
        
        name = data.get('name').strip()
        email = data.get('email').strip()
        whatsapp = data.get('whatsapp').strip()
        amount = int(data.get('amount', 0))
        
        # Validate email
        if '@' not in email or '.' not in email:
            return jsonify({
                'status': 'error',
                'message': 'Invalid email address'
            }), 400
        
        # Validate amount
        if amount < 0:
            return jsonify({
                'status': 'error',
                'message': 'Amount cannot be negative'
            }), 400
        
        # If amount is 0, don't create Razorpay order
        if amount == 0:
            order_id = f"FREE-{int(time.time())}"  # ✅ Use time module instead
            
            # Insert into database
            if insert_order(order_id, name, email, whatsapp, 0):
                # ✅ Send email immediately for free orders
                drive_link = os.getenv('BOOK_DRIVE_LINK', 'https://drive.google.com/file/d/1lBH-fdCcyfp6_ZUpph6nviZklm5d3Mwt/view?usp=drive_link')
                email_subject = '🎉 Code with Destiny - Your Free Access is Ready!'
                email_message = f'''
                Hello {name},
                
                Thank you for getting your free copy of "Code with Destiny"!
                
                Your book access is now active. Download it using the link in this email.
                
                Order Details:
                - Order ID: {order_id}
                - Status: ✅ Active
                - Access Type: Free
                '''
                
                send_email(email, email_subject, email_message, drive_link=drive_link)
                
                return jsonify({
                    'status': 'success',
                    'message': 'Free book access created. Email sent!',
                    'order_id': order_id,
                    'amount': 0,
                    'is_free': True
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to create order'
                }), 500
        
        # Create Razorpay order for paid purchases
        razorpay_order_data = {
            'amount': amount * 100,  # Convert to paise
            'currency': 'INR',
            'receipt': f'order-{int(datetime.now().timestamp())}',
            'notes': {
                'user_name': name,
                'user_email': email,
                'user_whatsapp': whatsapp
            }
        }
        
        razorpay_order = razorpay_client.order.create(razorpay_order_data)
        order_id = razorpay_order['id']
        
        # Store in database
        if insert_order(order_id, name, email, whatsapp, amount):
            return jsonify({
                'status': 'success',
                'message': 'Order created successfully',
                'order_id': order_id,
                'razorpay_order_id': order_id,
                'razorpay_key_id': os.getenv('RAZORPAY_KEY_ID'),
                'amount': amount,
                'is_free': False
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to save order'
            }), 500
    
    except Exception as e:
        print(f'❌ Order creation error: {e}')
        return jsonify({
            'status': 'error',
            'message': f'Server error: {str(e)}'
        }), 500

@app.route('/api/payments/verify', methods=['POST'])
def verify_payment():
    """
    Verify Razorpay payment signature
    
    Request body:
    {
        "razorpay_order_id": "order_id",
        "razorpay_payment_id": "payment_id",
        "razorpay_signature": "signature",
        "order_id": "our_order_id"
    }
    """
    try:
        data = request.get_json()
        
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id')
        razorpay_signature = data.get('razorpay_signature')
        our_order_id = data.get('order_id')
        
        if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature, our_order_id]):
            return jsonify({
                'status': 'error',
                'message': 'Missing payment verification data'
            }), 400
        
        # Verify signature
        body = razorpay_order_id + '|' + razorpay_payment_id
        expected_signature = hmac.new(
            os.getenv('RAZORPAY_KEY_SECRET').encode(),
            body.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if expected_signature == razorpay_signature:
            # Payment verified successfully
            update_order_payment(our_order_id, razorpay_payment_id, 'paid')
            
            # Get order details
            order = get_order(our_order_id)
            
            if order:
                # Google Drive link to your book
                drive_link = os.getenv('BOOK_DRIVE_LINK', 'https://drive.google.com/file/d/1lBH-fdCcyfp6_ZUpph6nviZklm5d3Mwt/view?usp=drive_link')
                
                # Send confirmation email ASYNCHRONOUSLY (non-blocking)
                email_subject = '🎉 Code with Destiny - Your Book is Ready!'
                email_message = f'''
                Thank you {order['user_name']} for purchasing "Code with Destiny"!
                
                Payment Details:
                - Order ID: {our_order_id}
                - Amount: ₹{order['amount']}
                - Payment ID: {razorpay_payment_id}
                - Status: ✅ PAID
                '''
                
                send_email_async(order['user_email'], email_subject, email_message, drive_link=drive_link)
            
            return jsonify({
                'status': 'success',
                'message': 'Payment verified successfully',
                'payment_id': razorpay_payment_id,
                'order_id': our_order_id
            })
        else:
            # Signature verification failed
            return jsonify({
                'status': 'error',
                'message': 'Payment verification failed - Invalid signature'
            }), 400
    
    except Exception as e:
        print(f'❌ Payment verification error: {e}')
        return jsonify({
            'status': 'error',
            'message': f'Verification error: {str(e)}'
        }), 500

@app.route('/api/orders/<order_id>', methods=['GET'])
def get_order_details(order_id):
    """Get order details"""
    try:
        order = get_order(order_id)
        
        if not order:
            return jsonify({
                'status': 'error',
                'message': 'Order not found'
            }), 404
        
        return jsonify({
            'status': 'success',
            'order': {
                'id': order['id'],
                'user_name': order['user_name'],
                'user_email': order['user_email'],
                'amount': order['amount'],
                'status': order['status'],
                'created_at': order['created_at']
            }
        })
    
    except Exception as e:
        print(f'❌ Error fetching order: {e}')
        return jsonify({
            'status': 'error',
            'message': f'Error: {str(e)}'
        }), 500

@app.route('/api/send-book', methods=['POST'])
def send_book():
    """
    Send book to user (called after successful payment or free purchase)
    
    Request body:
    {
        "order_id": "order_id",
        "email": "user@email.com"
    }
    """
    try:
        data = request.get_json()
        order_id = data.get('order_id')
        email = data.get('email')
        
        if not order_id or not email:
            return jsonify({
                'status': 'error',
                'message': 'Missing order_id or email'
            }), 400
        
        order = get_order(order_id)
        
        if not order:
            return jsonify({
                'status': 'error',
                'message': 'Order not found'
            }), 404
        
        # Send book (PDF attachment)
        # For production, use a proper email service with attachment capability
        email_subject = '📚 Code with Destiny - Your Book is Here!'
        
        # Google Drive link to your book
        drive_link = os.getenv('BOOK_DRIVE_LINK', 'https://drive.google.com/file/d/1lBH-fdCcyfp6_ZUpph6nviZklm5d3Mwt/view?usp=drive_link')
        
        email_message = f'''
        Hello {order['user_name']},
        
        Thank you for your interest in "Code with Destiny"!
        
        Your book is ready to download from the Google Drive link shared with this email.
        
        Order Details:
        - Order ID: {order_id}
        - Amount: ₹{order['amount']}
        - Status: {order['status']}
        
        Happy reading!
        
        Best regards,
        Code with Destiny Team
        '''
        
        send_email(email, email_subject, email_message, drive_link=drive_link)
        
        return jsonify({
            'status': 'success',
            'message': 'Book sent successfully',
            'order_id': order_id
        })
    
    except Exception as e:
        print(f'❌ Error sending book: {e}')
        return jsonify({
            'status': 'error',
            'message': f'Error: {str(e)}'
        }), 500

# ==================== Error Handlers ====================

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        'status': 'error',
        'message': 'Internal server error'
    }), 500

# ==================== Main ====================

if __name__ == '__main__':
    print('🚀 Starting Code with Destiny Backend...')
    print(f'🔑 Razorpay Key ID: {os.getenv("RAZORPAY_KEY_ID")}')
    print('💻 Server running on http://localhost:5000')
    app.run(debug=True, host='0.0.0.0', port=5000)



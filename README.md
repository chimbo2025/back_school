# School Portal Backend - Django REST API

Backend API for Bright Future School Zanzibar Online Admission & Fees Portal.

## Features

- Student admissions management
- Grade and fee structure management
- Payment tracking (M-Pesa, Mixx by Yas, Airtel Money, Bank)
- Finance dashboard with summaries
- Django Admin interface
- RESTful API with Django REST Framework

## Setup

1. Create virtual environment:
```bash
python -m venv venv
venv\Scripts\activate  # Windows
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment:
```bash
copy .env.example .env
# Edit .env with your settings
```

4. Run migrations:
```bash
python manage.py migrate
```

5. Seed initial data:
```bash
python manage.py seed_data
```

6. Create superuser (optional):
```bash
python manage.py createsuperuser
```

7. Run server:
```bash
python manage.py runserver
```

## API Endpoints

- `GET /api/grades/` - List all grades with fee structure
- `GET /api/grades/{id}/` - Get grade details
- `GET /api/fee-groups/` - List fee groups
- `GET /api/fee-items/` - List fee items
- `GET /api/students/` - List students (with filters: grade, status, academic_year, gender)
- `POST /api/students/` - Create new student application
- `GET /api/students/{id}/` - Get student details with payments
- `PATCH /api/students/{id}/update_status/` - Update student status
- `POST /api/students/{id}/add_payment/` - Add payment for student
- `GET /api/students/stats/` - Get admission statistics
- `GET /api/payments/` - List payments (with filters: student, method)
- `GET /api/finance/summary/` - Get finance dashboard summary

## Admin Interface

Access at `http://localhost:8000/admin/` after creating a superuser.

## Models

- **Grade**: School grades (Grade 1, 2, 3) with fee groups
- **FeeGroup**: Categories (SCHOOL FEE, TRANSPORT FEE, ADMISSION FEE)
- **FeeItem**: Individual fee items with amounts
- **Student**: Student applications with status tracking
- **Payment**: Payment records linked to students

## Frontend Integration

Configure CORS in `.env`:
```
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

The frontend runs on port 5173 (Vite default).
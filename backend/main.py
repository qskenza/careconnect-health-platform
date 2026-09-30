from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta, date
import jwt

# Support all PyJWT versions - create our own exception classes if needed
try:
    from jwt import ExpiredSignatureError
except (ImportError, AttributeError):
    # Create our own if not available
    class ExpiredSignatureError(Exception):
        """Token has expired"""
        pass

try:
    from jwt import InvalidTokenError
except (ImportError, AttributeError):
    try:
        from jwt import DecodeError as InvalidTokenError
    except (ImportError, AttributeError):
        # Create our own if not available
        class InvalidTokenError(Exception):
            """Invalid token error"""
            pass

import bcrypt
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from database import get_db
import models

load_dotenv()

app = FastAPI(title="CareConnect Health System API")

# ---------------------------------------------------------
# CORS CONFIG
# ---------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# SECURITY
# ---------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
security = HTTPBearer()


# ---------------------------------------------------------
# PASSWORD HASHING
# ---------------------------------------------------------
def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8")
    )


def generate_aui_email(full_name: str) -> str:
    """Generate AUI email: (first letter).(lastname)@aui.ma"""
    names = full_name.strip().split()
    if len(names) >= 2:
        return f"{names[0][0].lower()}.{names[-1].lower()}@aui.ma"
    return f"{names[0].lower()}@aui.ma"


# ---------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------
class AppointmentCreate(BaseModel):
    doctor_id: int
    appointment_date: str
    appointment_time: str
    type: str = "General Consultation"
    notes: Optional[str] = None


class AppointmentUpdate(BaseModel):
    appointment_date: Optional[str] = None
    appointment_time: Optional[str] = None
    type: Optional[str] = None
    notes: Optional[str] = None


class UserLogin(BaseModel):
    username: str
    password: str


class UserRegister(BaseModel):
    username: str
    password: str
    full_name: str
    student_id: str  # For students: numeric ID, for doctors/nurses: generated
    role: str = "student"  # student, doctor, or nurse
    # Student fields
    department: Optional[str] = None
    major: Optional[str] = None
    year_level: Optional[str] = None
    # Doctor fields
    license_number: Optional[str] = None
    specialization: Optional[str] = None
    # Nurse fields
    nursing_license: Optional[str] = None
    nurse_department: Optional[str] = None
    shift: Optional[str] = None
    # Common fields
    phone: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    department: Optional[str] = None
    major: Optional[str] = None
    year_level: Optional[str] = None


class EmergencyContactUpdate(BaseModel):
    name: str
    relationship: str
    phone: str
    email: Optional[str] = None


class MedicalEntryCreate(BaseModel):
    type: str
    name: str
    description: Optional[str] = None
    severity: Optional[str] = None
    diagnosed_date: Optional[str] = None


class MedicalEntryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    severity: Optional[str] = None
    is_active: Optional[bool] = None


class EmergencyRequestCreate(BaseModel):
    type: str
    description: str
    location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class DoctorAvailabilityCreate(BaseModel):
    day_of_week: int  # 0=Monday, 6=Sunday
    start_time: str   # "09:00 AM"
    end_time: str     # "05:00 PM"
    slot_duration: int = 30  # minutes

class DoctorAvailabilityUpdate(BaseModel):
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    slot_duration: Optional[int] = None
    is_active: Optional[bool] = None

class ProfessionalExperienceCreate(BaseModel):
    position: str
    institution: str
    start_date: str  # Format: "YYYY-MM-DD"
    end_date: Optional[str] = None  # Format: "YYYY-MM-DD", None if current
    description: Optional[str] = None
    is_current: bool = False

class ProfessionalExperienceUpdate(BaseModel):
    position: Optional[str] = None
    institution: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    description: Optional[str] = None
    is_current: Optional[bool] = None

class DoctorProfileUpdate(BaseModel):
    specialty: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

class PrescriptionCreate(BaseModel):
    patient_id: int
    medication: str
    dosage: str
    frequency: str
    duration: str
    instructions: Optional[str] = None

class ReferralCreate(BaseModel):
    patient_id: int
    specialist_type: str
    reason: str
    priority: str = "routine"
    notes: Optional[str] = None

class DoctorMedicalRecordCreate(BaseModel):
    patient_id: int
    type: str
    name: str
    severity: Optional[str] = None
    description: Optional[str] = None


# ---------------------------------------------------------
# SCHEDULING HELPERS
# ---------------------------------------------------------
def slot_datetime(day, time_str):
    """Combine a date and a '09:30 AM' string into a datetime."""
    return datetime.combine(day, datetime.strptime(time_str, "%I:%M %p").time())


def ensure_future_slot(day, time_str):
    """Reject dates in the past and times that have already passed today."""
    if day < datetime.now().date():
        raise HTTPException(400, "You cannot book an appointment on a past date")
    try:
        when = slot_datetime(day, time_str)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid time format. Use e.g. 09:30 AM")
    if when <= datetime.now():
        raise HTTPException(400, "This time has already passed. Please choose a later slot")


def ensure_slot_free(db, doctor_id, day, time_str, exclude_id=None):
    query = db.query(models.Appointment).filter(
        models.Appointment.doctor_id == doctor_id,
        models.Appointment.appointment_date == day,
        models.Appointment.appointment_time == time_str,
        models.Appointment.status != "cancelled",
    )
    if exclude_id:
        query = query.filter(models.Appointment.id != exclude_id)
    if query.first():
        raise HTTPException(400, "This time slot is already booked")


def get_doctor_or_404(db, user):
    doctor = db.query(models.Doctor).filter(models.Doctor.user_id == user.id).first()
    if not doctor:
        raise HTTPException(404, "Doctor profile not found")
    return doctor


def require_role(user, *roles):
    if user.role not in roles:
        raise HTTPException(403, "Access denied")


# ---------------------------------------------------------
# TOKEN & AUTH
# ---------------------------------------------------------
def create_access_token(data: dict):
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(hours=24)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
):
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")

        user = db.query(models.User).filter(
            models.User.username == username
        ).first()

        if not user:
            raise HTTPException(401, "User not found")
        if user.is_active is False:
            raise HTTPException(401, "Account deactivated")

        return user

    except ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    except Exception as e:
        # Catch any other JWT-related exceptions
        if 'expired' in str(e).lower():
            raise HTTPException(401, "Token expired")
        raise HTTPException(401, "Invalid token")


# ---------------------------------------------------------
# ROOT
# ---------------------------------------------------------
@app.get("/")
def root():
    return {
        "message": "CareConnect Health System API",
        "version": "2.0.0",
        "status": "active"
    }


# ---------------------------------------------------------
# AUTH
# ---------------------------------------------------------
@app.post("/auth/register")
def register(user: UserRegister, db: Session = Depends(get_db)):
    # Check if username already exists
    existing_user = db.query(models.User).filter(
        models.User.username == user.username
    ).first()
    
    if existing_user:
        raise HTTPException(400, "Username already exists")

    # For students, check if student_id already exists
    if user.role == "student":
        existing_student = db.query(models.User).filter(
            models.User.student_id == user.student_id
        ).first()
        if existing_student:
            raise HTTPException(400, "Student ID already exists")

    # Validate role-specific fields
    if user.role == "student":
        if not user.department or not user.major:
            raise HTTPException(400, "Department and Major required for students")
        if user.department not in ["SSE", "SBA", "SSAH"]:
            raise HTTPException(400, "Invalid department. Must be SSE, SBA, or SSAH")
    elif user.role == "doctor":
        if not user.license_number or not user.specialization:
            raise HTTPException(400, "License number and Specialization required for doctors")
        # Check if license number already exists
        existing_doctor = db.query(models.Doctor).filter(
            models.Doctor.license_number == user.license_number
        ).first()
        if existing_doctor:
            raise HTTPException(400, "License number already registered")
    elif user.role == "nurse":
        if not user.nursing_license or not user.nurse_department:
            raise HTTPException(400, "Nursing license and Department required for nurses")
        # Check if nursing license already exists
        existing_nurse = db.query(models.Nurse).filter(
            models.Nurse.license_number == user.nursing_license
        ).first()
        if existing_nurse:
            raise HTTPException(400, "Nursing license number already registered")
    else:
        raise HTTPException(400, "Invalid role. Must be 'student', 'doctor', or 'nurse'")

    # Generate AUI email
    email = generate_aui_email(user.full_name)
    
    # Check if email already exists and make it unique if needed
    base_email = email
    counter = 1
    while db.query(models.User).filter(models.User.email == email).first():
        name_parts = base_email.split('@')
        email = f"{name_parts[0]}{counter}@{name_parts[1]}"
        counter += 1

    # Generate student_id for doctors and nurses if not provided properly
    if user.role == "doctor":
        # Use a numeric ID for doctors based on timestamp
        import time
        doctor_id = str(int(time.time()))[-7:]  # Last 7 digits of timestamp
        student_id = f"D{doctor_id}"
    elif user.role == "nurse":
        # Use a numeric ID for nurses based on timestamp
        import time
        nurse_id = str(int(time.time()))[-7:]  # Last 7 digits of timestamp
        student_id = f"N{nurse_id}"
    else:
        student_id = user.student_id

    # Create user
    db_user = models.User(
        username=user.username,
        password_hash=hash_password(user.password),
        full_name=user.full_name,
        email=email,
        student_id=student_id,
        institution="Al Akhawayn University",
        department=user.department if user.role == "student" else None,
        major=user.major if user.role == "student" else (user.specialization if user.role == "doctor" else None),
        academic_year="2025/2026",
        phone=user.phone,
        date_of_birth=datetime.strptime(user.date_of_birth, "%Y-%m-%d").date()
        if user.date_of_birth else None,
        gender=user.gender,
        year_level=user.year_level if user.role == "student" else None,
        role=user.role,
    )

    db.add(db_user)
    db.flush()  # Get the user ID

    # If doctor, create doctor entry linked to user
    if user.role == "doctor":
        # Generate avatar initials
        name_parts = user.full_name.split()
        avatar = ''.join([n[0].upper() for n in name_parts[:2]]) if len(name_parts) >= 2 else user.full_name[:2].upper()

        doctor = models.Doctor(
            user_id=db_user.id,
            name=user.full_name,
            license_number=user.license_number,
            specialty=user.specialization,
            email=email,
            phone=user.phone,
            avatar=avatar,
            rating=0.0,
            reviews_count=0,
            is_available=True
        )
        db.add(doctor)

    # If nurse, create nurse entry linked to user
    elif user.role == "nurse":
        # Generate avatar initials
        name_parts = user.full_name.split()
        avatar = ''.join([n[0].upper() for n in name_parts[:2]]) if len(name_parts) >= 2 else user.full_name[:2].upper()

        nurse = models.Nurse(
            user_id=db_user.id,
            name=user.full_name,
            license_number=user.nursing_license,
            department=user.nurse_department,
            email=email,
            phone=user.phone,
            avatar=avatar,
            shift=user.shift,
            is_available=True
        )
        db.add(nurse)

    db.commit()
    db.refresh(db_user)

    token = create_access_token({"sub": db_user.username})

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "username": db_user.username,
            "full_name": db_user.full_name,
            "email": db_user.email,
            "student_id": db_user.student_id,
            "role": db_user.role,
            "department": db_user.department,
            "major": db_user.major
        }
    }


@app.post("/auth/login")
def login(user: UserLogin, db: Session = Depends(get_db)):
    db_user = db.query(models.User).filter(
        models.User.username == user.username
    ).first()

    if not db_user or not verify_password(user.password, db_user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    if db_user.is_active is False:
        raise HTTPException(403, "This account has been deactivated. Contact the administrator.")

    token = create_access_token({"sub": db_user.username})

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "username": db_user.username,
            "full_name": db_user.full_name,
            "email": db_user.email,
            "student_id": db_user.student_id,
            "role": db_user.role,
            "department": db_user.department,
            "major": db_user.major
        }
    }


# ---------------------------------------------------------
# PROFILE
# ---------------------------------------------------------
@app.get("/profile")
def get_profile(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    emergency = db.query(models.EmergencyContact).filter(
        models.EmergencyContact.user_id == current_user.id
    ).first()

    response = {
        "username": current_user.username,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "student_id": current_user.student_id,
        "department": current_user.department,
        "major": current_user.major,
        "phone": current_user.phone,
        "date_of_birth": current_user.date_of_birth.isoformat()
        if current_user.date_of_birth else None,
        "gender": current_user.gender,
        "year_level": current_user.year_level,
        "role": current_user.role,
        "emergency_contact": {
            "name": emergency.name,
            "relationship": emergency.relationship,
            "phone": emergency.phone,
            "email": emergency.email
        } if emergency else None,
    }
    
    # If doctor, add doctor-specific info
    if current_user.role == "doctor":
        doctor = db.query(models.Doctor).filter(
            models.Doctor.user_id == current_user.id
        ).first()
        if doctor:
            response["license_number"] = doctor.license_number
            response["specialty"] = doctor.specialty
            response["rating"] = doctor.rating
            response["reviews_count"] = doctor.reviews_count
    
    return response


@app.put("/profile/update")
def update_profile(
    updates: UserUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if updates.full_name:
        current_user.full_name = updates.full_name
        current_user.email = generate_aui_email(updates.full_name)
        
        # Update doctor name if applicable
        if current_user.role == "doctor":
            doctor = db.query(models.Doctor).filter(
                models.Doctor.user_id == current_user.id
            ).first()
            if doctor:
                doctor.name = updates.full_name
                doctor.email = current_user.email

    if updates.phone:
        current_user.phone = updates.phone

    if updates.date_of_birth:
        current_user.date_of_birth = datetime.strptime(
            updates.date_of_birth, "%Y-%m-%d"
        ).date()

    if updates.gender:
        current_user.gender = updates.gender

    if updates.department:
        if updates.department not in ["SSE", "SBA", "SSAH"]:
            raise HTTPException(400, "Invalid department")
        current_user.department = updates.department

    if updates.major:
        current_user.major = updates.major

    if updates.year_level:
        current_user.year_level = updates.year_level

    db.commit()
    db.refresh(current_user)

    return {
        "message": "Profile updated",
        "email": current_user.email
    }


@app.put("/profile/emergency-contact")
def update_emergency_contact(
    contact: EmergencyContactUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.query(models.EmergencyContact).filter(
        models.EmergencyContact.user_id == current_user.id
    ).first()

    if existing:
        existing.name = contact.name
        existing.relationship = contact.relationship
        existing.phone = contact.phone
        existing.email = contact.email
    else:
        new_contact = models.EmergencyContact(
            user_id=current_user.id,
            name=contact.name,
            relationship=contact.relationship,
            phone=contact.phone,
            email=contact.email,
        )
        db.add(new_contact)

    db.commit()
    return {"message": "Emergency contact updated"}


# ---------------------------------------------------------
# MEDICAL RECORDS
# ---------------------------------------------------------
@app.get("/medical-records")
def get_medical_records(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    records = db.query(models.MedicalRecord).filter(
        models.MedicalRecord.user_id == current_user.id,
        models.MedicalRecord.is_active == True
    ).all()

    return {
        "allergies": [
            {"id": r.id, "name": r.name, "severity": r.severity, "description": r.description}
            for r in records if r.type == "allergy"
        ],
        "medications": [
            {"id": r.id, "name": r.name, "description": r.description}
            for r in records if r.type == "medication"
        ],
        "conditions": [
            {"id": r.id, "name": r.name, "severity": r.severity, "description": r.description}
            for r in records if r.type == "condition"
        ],
    }


@app.post("/medical-records/entry")
def add_medical_entry(
    entry: MedicalEntryCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if entry.type not in ["allergy", "medication", "condition"]:
        raise HTTPException(400, "Invalid entry type")

    db_entry = models.MedicalRecord(
        user_id=current_user.id,
        type=entry.type,
        name=entry.name,
        description=entry.description,
        severity=entry.severity,
        diagnosed_date=datetime.strptime(entry.diagnosed_date, "%Y-%m-%d").date()
        if entry.diagnosed_date else None,
    )

    db.add(db_entry)
    db.commit()
    db.refresh(db_entry)

    return {"message": "Entry added", "id": db_entry.id}


@app.put("/medical-records/{entry_id}")
def update_medical_entry(
    entry_id: int,
    updates: MedicalEntryUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = db.query(models.MedicalRecord).filter(
        models.MedicalRecord.id == entry_id,
        models.MedicalRecord.user_id == current_user.id,
    ).first()

    if not entry:
        raise HTTPException(404, "Entry not found")

    if updates.name:
        entry.name = updates.name
    if updates.description:
        entry.description = updates.description
    if updates.severity:
        entry.severity = updates.severity
    if updates.is_active is not None:
        entry.is_active = updates.is_active

    db.commit()
    return {"message": "Entry updated"}


@app.delete("/medical-records/{entry_id}")
def delete_medical_entry(
    entry_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = db.query(models.MedicalRecord).filter(
        models.MedicalRecord.id == entry_id,
        models.MedicalRecord.user_id == current_user.id,
    ).first()

    if not entry:
        raise HTTPException(404, "Entry not found")

    entry.is_active = False
    db.commit()
    return {"message": "Entry deleted"}


# ---------------------------------------------------------
# VISITS
# ---------------------------------------------------------
@app.get("/visits/all")
def get_all_visits(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    visits = db.query(models.Visit).filter(
        models.Visit.user_id == current_user.id
    ).order_by(models.Visit.visit_date.desc()).all()

    # Booked appointments that haven't happened yet (or were cancelled)
    appointments = db.query(models.Appointment).filter(
        models.Appointment.user_id == current_user.id,
        models.Appointment.status.in_(["upcoming", "cancelled"])
    ).order_by(models.Appointment.appointment_date.desc()).all()
    appointment_items = [
        {
            "id": f"appt-{a.id}",
            "appointment_id": a.id,
            "date": a.appointment_date.isoformat(),
            "time_start": a.appointment_time,
            "time_end": None,
            "doctor_name": a.doctor.name if a.doctor else "Unknown",
            "diagnosis": a.type,
            "type": a.type,
            "location": a.location,
            "notes": a.notes,
            "status": a.status,
        }
        for a in appointments
    ]

    total = len(visits) + len(appointment_items)
    upcoming = len([a for a in appointment_items if a["status"] == "upcoming"])
    completed = len([v for v in visits if v.status == "completed"])
    cancelled = len([a for a in appointment_items if a["status"] == "cancelled"])
    
    return {
        "statistics": {
            "total": total,
            "upcoming": upcoming,
            "completed": completed,
            "cancelled": cancelled
        },
        "visits": [
            {
                "id": v.id,
                "date": v.visit_date.isoformat(),
                "time_start": v.time_start,
                "time_end": v.time_end,
                "doctor_name": v.doctor.name if v.doctor else "Unknown",
                "diagnosis": v.diagnosis,
                "type": v.type,
                "location": v.location,
                "notes": v.notes,
                "status": v.status
            }
            for v in visits
        ] + appointment_items
    }


# ---------------------------------------------------------
# DOCTORS
# ---------------------------------------------------------
@app.get("/doctors")
def get_doctors(db: Session = Depends(get_db)):
    # Only doctors whose accounts are active can be booked
    doctors = db.query(models.Doctor).filter(models.Doctor.is_available == True).all()

    return [
        {
            "id": d.id,
            "name": d.name,
            "specialty": d.specialty,
            "rating": d.rating,
            "reviews": d.reviews_count,
            "avatar": d.avatar,
            "email": d.email,
            "phone": d.phone,
            "is_available": d.is_available
        }
        for d in doctors
    ]


@app.put("/doctor/profile")
def update_doctor_profile(
    update_data: DoctorProfileUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update doctor's profile information"""
    doctor = db.query(models.Doctor).filter(models.Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    # Update doctor fields
    if update_data.specialty is not None:
        doctor.specialty = update_data.specialty
    if update_data.email is not None:
        doctor.email = update_data.email
    if update_data.phone is not None:
        doctor.phone = update_data.phone

    db.commit()
    db.refresh(doctor)

    return {
        "message": "Profile updated successfully",
        "specialty": doctor.specialty,
        "email": doctor.email,
        "phone": doctor.phone
    }


# ---------------------------------------------------------
# PROFESSIONAL EXPERIENCE
# ---------------------------------------------------------
@app.get("/doctor/professional-experience")
def get_my_professional_experience(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current doctor's professional experience"""
    doctor = db.query(models.Doctor).filter(models.Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    experiences = db.query(models.ProfessionalExperience).filter(
        models.ProfessionalExperience.doctor_id == doctor.id
    ).order_by(models.ProfessionalExperience.is_current.desc(), models.ProfessionalExperience.start_date.desc()).all()

    return [
        {
            "id": exp.id,
            "position": exp.position,
            "institution": exp.institution,
            "start_date": exp.start_date.isoformat() if exp.start_date else None,
            "end_date": exp.end_date.isoformat() if exp.end_date else None,
            "description": exp.description,
            "is_current": exp.is_current
        }
        for exp in experiences
    ]


@app.get("/doctors/{doctor_id}/professional-experience")
def get_doctor_professional_experience(doctor_id: int, db: Session = Depends(get_db)):
    """Get professional experience for a specific doctor (public endpoint for students)"""
    doctor = db.query(models.Doctor).filter(models.Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    experiences = db.query(models.ProfessionalExperience).filter(
        models.ProfessionalExperience.doctor_id == doctor_id
    ).order_by(models.ProfessionalExperience.is_current.desc(), models.ProfessionalExperience.start_date.desc()).all()

    return [
        {
            "id": exp.id,
            "position": exp.position,
            "institution": exp.institution,
            "start_date": exp.start_date.isoformat() if exp.start_date else None,
            "end_date": exp.end_date.isoformat() if exp.end_date else None,
            "description": exp.description,
            "is_current": exp.is_current
        }
        for exp in experiences
    ]


@app.post("/doctor/professional-experience")
def create_professional_experience(
    experience_data: ProfessionalExperienceCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new professional experience entry"""
    doctor = db.query(models.Doctor).filter(models.Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    # Parse dates
    try:
        start_date = datetime.strptime(experience_data.start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(experience_data.end_date, "%Y-%m-%d").date() if experience_data.end_date else None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    experience = models.ProfessionalExperience(
        doctor_id=doctor.id,
        position=experience_data.position,
        institution=experience_data.institution,
        start_date=start_date,
        end_date=end_date,
        description=experience_data.description,
        is_current=experience_data.is_current
    )

    db.add(experience)
    db.commit()
    db.refresh(experience)

    return {
        "message": "Professional experience added successfully",
        "id": experience.id,
        "position": experience.position,
        "institution": experience.institution,
        "start_date": experience.start_date.isoformat(),
        "end_date": experience.end_date.isoformat() if experience.end_date else None,
        "description": experience.description,
        "is_current": experience.is_current
    }


@app.put("/doctor/professional-experience/{experience_id}")
def update_professional_experience(
    experience_id: int,
    experience_data: ProfessionalExperienceUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a professional experience entry"""
    doctor = db.query(models.Doctor).filter(models.Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    experience = db.query(models.ProfessionalExperience).filter(
        models.ProfessionalExperience.id == experience_id,
        models.ProfessionalExperience.doctor_id == doctor.id
    ).first()

    if not experience:
        raise HTTPException(status_code=404, detail="Professional experience not found")

    # Update fields
    if experience_data.position is not None:
        experience.position = experience_data.position
    if experience_data.institution is not None:
        experience.institution = experience_data.institution
    if experience_data.start_date is not None:
        try:
            experience.start_date = datetime.strptime(experience_data.start_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD")
    if experience_data.end_date is not None:
        try:
            experience.end_date = datetime.strptime(experience_data.end_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD")
    if experience_data.description is not None:
        experience.description = experience_data.description
    if experience_data.is_current is not None:
        experience.is_current = experience_data.is_current

    db.commit()
    db.refresh(experience)

    return {
        "message": "Professional experience updated successfully",
        "id": experience.id,
        "position": experience.position,
        "institution": experience.institution,
        "start_date": experience.start_date.isoformat(),
        "end_date": experience.end_date.isoformat() if experience.end_date else None,
        "description": experience.description,
        "is_current": experience.is_current
    }


@app.delete("/doctor/professional-experience/{experience_id}")
def delete_professional_experience(
    experience_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a professional experience entry"""
    doctor = db.query(models.Doctor).filter(models.Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    experience = db.query(models.ProfessionalExperience).filter(
        models.ProfessionalExperience.id == experience_id,
        models.ProfessionalExperience.doctor_id == doctor.id
    ).first()

    if not experience:
        raise HTTPException(status_code=404, detail="Professional experience not found")

    db.delete(experience)
    db.commit()

    return {"message": "Professional experience deleted successfully"}


# ---------------------------------------------------------
# EMERGENCY
# ---------------------------------------------------------
@app.post("/emergency")
def create_emergency_request(
    request: EmergencyRequestCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    emergency = models.EmergencyRequest(
        user_id=current_user.id,
        type=request.type,
        description=request.description,
        location=request.location,
        latitude=request.latitude,
        longitude=request.longitude,
        status="active",
        priority="high",
    )

    db.add(emergency)
    db.commit()

    return {"message": "Emergency request created", "id": emergency.id}


# ---------------------------------------------------------
# APPOINTMENTS
# ---------------------------------------------------------
@app.get("/appointments")
def get_appointments(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all appointments for current user"""
    appointments = db.query(models.Appointment).filter(
        models.Appointment.user_id == current_user.id
    ).order_by(models.Appointment.appointment_date.desc()).all()
    
    return [
        {
            "id": a.id,
            "doctor_id": a.doctor_id,
            "doctor_name": a.doctor.name if a.doctor else "Unknown",
            "doctor_specialty": a.doctor.specialty if a.doctor else "",
            "date": a.appointment_date.isoformat() if a.appointment_date else None,
            "time": a.appointment_time,
            "type": a.type,
            "location": a.location,
            "status": a.status,
            "notes": a.notes,
            "can_reschedule": a.can_reschedule
        }
        for a in appointments
    ]


@app.post("/appointments")
def create_appointment(
    appointment: AppointmentCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new appointment"""
    # Verify doctor exists
    doctor = db.query(models.Doctor).filter(
        models.Doctor.id == appointment.doctor_id
    ).first()
    
    if not doctor:
        raise HTTPException(404, "Doctor not found")
    
    # Parse date
    try:
        appt_date = datetime.strptime(appointment.appointment_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
    
    if current_user.role != "student":
        raise HTTPException(403, "Only students can book appointments")

    ensure_future_slot(appt_date, appointment.appointment_time)
    ensure_slot_free(db, appointment.doctor_id, appt_date, appointment.appointment_time)
    
    # Create appointment
    db_appointment = models.Appointment(
        user_id=current_user.id,
        doctor_id=appointment.doctor_id,
        appointment_date=appt_date,
        appointment_time=appointment.appointment_time,
        type=appointment.type,
        location=f"Campus Health Center, Room {doctor.id}01",
        notes=appointment.notes,
        status="upcoming",
        can_reschedule=True
    )
    
    db.add(db_appointment)
    db.commit()
    db.refresh(db_appointment)
    
    return {
        "message": "Appointment created successfully",
        "id": db_appointment.id,
        "date": db_appointment.appointment_date.isoformat(),
        "time": db_appointment.appointment_time,
        "doctor": doctor.name
    }


@app.put("/appointments/{appointment_id}")
def update_appointment(
    appointment_id: int,
    updates: AppointmentUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Reschedule an appointment"""
    appointment = db.query(models.Appointment).filter(
        models.Appointment.id == appointment_id,
        models.Appointment.user_id == current_user.id
    ).first()
    
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    
    if appointment.status == "cancelled":
        raise HTTPException(400, "Cannot modify a cancelled appointment")
    
    # Check 12-hour rule
    if appointment.appointment_date:
        appt_time = datetime.strptime(appointment.appointment_time, "%I:%M %p").time() if appointment.appointment_time else datetime.min.time()
        appt_datetime = datetime.combine(appointment.appointment_date, appt_time)
        hours_until = (appt_datetime - datetime.now()).total_seconds() / 3600
        if hours_until < 12:
            raise HTTPException(400, "Cannot reschedule within 12 hours of appointment")
    
    # Validate the new date/time before saving
    try:
        new_date = datetime.strptime(updates.appointment_date, "%Y-%m-%d").date() if updates.appointment_date else appointment.appointment_date
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
    new_time = updates.appointment_time or appointment.appointment_time
    if updates.appointment_date or updates.appointment_time:
        ensure_future_slot(new_date, new_time)
        ensure_slot_free(db, appointment.doctor_id, new_date, new_time, exclude_id=appointment.id)

    # Update fields
    appointment.appointment_date = new_date
    appointment.appointment_time = new_time
    if updates.type:
        appointment.type = updates.type
    if updates.notes:
        appointment.notes = updates.notes
    
    db.commit()
    
    return {"message": "Appointment updated successfully"}


@app.delete("/appointments/{appointment_id}")
def cancel_appointment(
    appointment_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel an appointment"""
    appointment = db.query(models.Appointment).filter(
        models.Appointment.id == appointment_id,
        models.Appointment.user_id == current_user.id
    ).first()
    
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    
    appointment.status = "cancelled"
    appointment.can_reschedule = False
    db.commit()
    
    return {"message": "Appointment cancelled successfully"}


@app.get("/appointments/upcoming")
def get_upcoming_appointments(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get upcoming appointments (future appointments that are not cancelled)"""
    today = datetime.now().date()
    
    appointments = db.query(models.Appointment).filter(
        models.Appointment.user_id == current_user.id,
        models.Appointment.appointment_date >= today,
        models.Appointment.status != "cancelled"
    ).order_by(models.Appointment.appointment_date.asc()).all()
    
    result = []
    for a in appointments:
        # Calculate if can reschedule (12 hour rule)
        try:
            appointment_datetime = slot_datetime(a.appointment_date, a.appointment_time)
        except (ValueError, TypeError):
            appointment_datetime = datetime.combine(a.appointment_date, datetime.min.time())
        if appointment_datetime < datetime.now():
            continue  # already passed today
        hours_until = (appointment_datetime - datetime.now()).total_seconds() / 3600
        can_reschedule = hours_until > 12
        
        result.append({
            "id": a.id,
            "doctor_id": a.doctor_id,
            "doctor_name": a.doctor.name if a.doctor else "Unknown",
            "doctor_specialty": a.doctor.specialty if a.doctor else "",
            "date": a.appointment_date.isoformat() if a.appointment_date else None,
            "time": a.appointment_time,
            "type": a.type,
            "location": a.location,
            "status": a.status,
            "notes": a.notes,
            "can_reschedule": can_reschedule,
            "hours_until": hours_until
        })
    
    return result


@app.post("/appointments/{appointment_id}/complete")
def complete_appointment(
    appointment_id: int,
    diagnosis: str = None,
    notes: str = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Mark an appointment as completed and create a visit record.
    This should be called by doctors or automatically after the appointment time.
    """
    require_role(current_user, "doctor", "admin")
    appointment = db.query(models.Appointment).filter(
        models.Appointment.id == appointment_id
    ).first()
    
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    if current_user.role == "doctor" and appointment.doctor_id != get_doctor_or_404(db, current_user).id:
        raise HTTPException(403, "This appointment belongs to another doctor")
    if appointment.status != "upcoming":
        raise HTTPException(400, f"Appointment is already {appointment.status}")
    
    # Update appointment status
    appointment.status = "completed"
    
    # Create a visit record from the completed appointment
    visit = models.Visit(
        user_id=appointment.user_id,
        doctor_id=appointment.doctor_id,
        visit_date=appointment.appointment_date,
        time_start=appointment.appointment_time,
        time_end=appointment.appointment_time,  # You can calculate end time
        diagnosis=diagnosis or "General Consultation",
        type=appointment.type,
        location=appointment.location,
        notes=notes or appointment.notes or "Appointment completed",
        status="completed"
    )
    
    db.add(visit)
    db.commit()
    db.refresh(visit)
    
    return {
        "message": "Appointment completed and added to visit history",
        "visit_id": visit.id,
        "appointment_id": appointment.id
    }


@app.get("/visits/recent")
def get_recent_visits(
    limit: int = 3,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get recent completed visits"""
    visits = db.query(models.Visit).filter(
        models.Visit.user_id == current_user.id,
        models.Visit.status == "completed"
    ).order_by(models.Visit.visit_date.desc()).limit(limit).all()
    
    return [
        {
            "id": v.id,
            "date": v.visit_date.isoformat(),
            "time_start": v.time_start,
            "time_end": v.time_end,
            "doctor_name": v.doctor.name if v.doctor else "Unknown",
            "diagnosis": v.diagnosis,
            "type": v.type,
            "location": v.location,
            "notes": v.notes,
            "status": v.status
        }
        for v in visits
    ]

# ---------------------------------------------------------
# PERMANENT DELETE MEDICAL RECORD
# ---------------------------------------------------------
@app.delete("/medical-records/{entry_id}/permanent")
def permanently_delete_medical_entry(
    entry_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permanently delete a medical entry"""
    entry = db.query(models.MedicalRecord).filter(
        models.MedicalRecord.id == entry_id,
        models.MedicalRecord.user_id == current_user.id,
    ).first()

    if not entry:
        raise HTTPException(404, "Entry not found")

    db.delete(entry)
    db.commit()
    return {"message": "Entry permanently deleted"}


# ---------------------------------------------------------
# DOCTOR DASHBOARD ENDPOINTS
# ---------------------------------------------------------
@app.get("/doctor/patients")
def get_doctor_patients(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get patients for a doctor"""
    if current_user.role != "doctor":
        raise HTTPException(403, "Access denied. Doctors only.")
    
    # Get doctor record
    doctor = db.query(models.Doctor).filter(
        models.Doctor.user_id == current_user.id
    ).first()
    
    if not doctor:
        raise HTTPException(404, "Doctor profile not found")
    
    # Get today's appointments
    today = datetime.now().date()
    appointments = db.query(models.Appointment).filter(
        models.Appointment.doctor_id == doctor.id,
        models.Appointment.appointment_date == today,
        models.Appointment.status == "upcoming"
    ).all()
    
    return [
        {
            "id": a.id,
            "patient_name": a.user.full_name if a.user else "Unknown",
            "patient_id": a.user.student_id if a.user else "N/A",
            "time": a.appointment_time,
            "type": a.type,
            "notes": a.notes
        }
        for a in appointments
    ]


@app.get("/doctor/schedule")
def get_doctor_schedule(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get doctor's schedule"""
    if current_user.role != "doctor":
        raise HTTPException(403, "Access denied. Doctors only.")
    
    doctor = db.query(models.Doctor).filter(
        models.Doctor.user_id == current_user.id
    ).first()
    
    if not doctor:
        raise HTTPException(404, "Doctor profile not found")
    
    # Get all upcoming appointments
    appointments = db.query(models.Appointment).filter(
        models.Appointment.doctor_id == doctor.id,
        models.Appointment.status == "upcoming"
    ).order_by(models.Appointment.appointment_date).all()
    
    return {
        "doctor_name": doctor.name,
        "specialty": doctor.specialty,
        "appointments": [
            {
                "id": a.id,
                "patient_name": a.user.full_name if a.user else "Unknown",
                "patient_user_id": a.user_id,
                "patient_id": a.user.student_id if a.user else "N/A",
                "date": a.appointment_date.isoformat() if a.appointment_date else None,
                "time": a.appointment_time,
                "type": a.type,
                "notes": a.notes,
                "status": a.status
            }
            for a in appointments
        ]
    }

@app.get("/doctor/availability")
def get_doctor_availability(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get doctor's availability schedule"""
    if current_user.role != 'doctor':
        raise HTTPException(403, "Access denied. Doctors only.")
    
    doctor = db.query(models.Doctor).filter(
        models.Doctor.user_id == current_user.id
    ).first()
    
    if not doctor:
        raise HTTPException(404, "Doctor profile not found")
    
    availability = db.query(models.DoctorAvailability).filter(
        models.DoctorAvailability.doctor_id == doctor.id,
        models.DoctorAvailability.is_active == True
    ).order_by(models.DoctorAvailability.day_of_week).all()
    
    return [
        {
            "id": a.id,
            "day_of_week": a.day_of_week,
            "day_name": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][a.day_of_week],
            "start_time": a.start_time,
            "end_time": a.end_time,
            "slot_duration": a.slot_duration,
            "is_active": a.is_active
        }
        for a in availability
    ]


@app.post("/doctor/availability")
def create_doctor_availability(
    availability: DoctorAvailabilityCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create new availability slot for doctor"""
    if current_user.role != 'doctor':
        raise HTTPException(403, "Access denied. Doctors only.")
    
    doctor = db.query(models.Doctor).filter(
        models.Doctor.user_id == current_user.id
    ).first()
    
    if not doctor:
        raise HTTPException(404, "Doctor profile not found")
    
    # Validate day_of_week
    if availability.day_of_week < 0 or availability.day_of_week > 6:
        raise HTTPException(400, "day_of_week must be between 0 (Monday) and 6 (Sunday)")
    
    # Check if availability already exists for this day
    existing = db.query(models.DoctorAvailability).filter(
        models.DoctorAvailability.doctor_id == doctor.id,
        models.DoctorAvailability.day_of_week == availability.day_of_week,
        models.DoctorAvailability.is_active == True
    ).first()
    
    if existing:
        raise HTTPException(400, f"Availability already exists for this day. Update or delete it first.")
    
    # Create new availability
    db_availability = models.DoctorAvailability(
        doctor_id=doctor.id,
        day_of_week=availability.day_of_week,
        start_time=availability.start_time,
        end_time=availability.end_time,
        slot_duration=availability.slot_duration
    )
    
    db.add(db_availability)
    db.commit()
    db.refresh(db_availability)
    
    return {
        "message": "Availability created successfully",
        "id": db_availability.id,
        "day": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][db_availability.day_of_week]
    }


@app.put("/doctor/availability/{availability_id}")
def update_doctor_availability(
    availability_id: int,
    updates: DoctorAvailabilityUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update doctor availability"""
    if current_user.role != 'doctor':
        raise HTTPException(403, "Access denied. Doctors only.")
    
    doctor = db.query(models.Doctor).filter(
        models.Doctor.user_id == current_user.id
    ).first()
    
    if not doctor:
        raise HTTPException(404, "Doctor profile not found")
    
    availability = db.query(models.DoctorAvailability).filter(
        models.DoctorAvailability.id == availability_id,
        models.DoctorAvailability.doctor_id == doctor.id
    ).first()
    
    if not availability:
        raise HTTPException(404, "Availability not found")
    
    # Update fields
    if updates.start_time:
        availability.start_time = updates.start_time
    if updates.end_time:
        availability.end_time = updates.end_time
    if updates.slot_duration:
        availability.slot_duration = updates.slot_duration
    if updates.is_active is not None:
        availability.is_active = updates.is_active
    
    availability.updated_at = datetime.utcnow()
    db.commit()
    
    return {"message": "Availability updated successfully"}


@app.delete("/doctor/availability/{availability_id}")
def delete_doctor_availability(
    availability_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete doctor availability"""
    if current_user.role != 'doctor':
        raise HTTPException(403, "Access denied. Doctors only.")
    
    doctor = db.query(models.Doctor).filter(
        models.Doctor.user_id == current_user.id
    ).first()
    
    if not doctor:
        raise HTTPException(404, "Doctor profile not found")
    
    availability = db.query(models.DoctorAvailability).filter(
        models.DoctorAvailability.id == availability_id,
        models.DoctorAvailability.doctor_id == doctor.id
    ).first()
    
    if not availability:
        raise HTTPException(404, "Availability not found")
    
    db.delete(availability)
    db.commit()
    
    return {"message": "Availability deleted successfully"}


def parse_time_to_minutes(time_str: str) -> int:
    """Convert time string like '09:00 AM' to minutes since midnight"""
    try:
        time_obj = datetime.strptime(time_str, "%I:%M %p")
        return time_obj.hour * 60 + time_obj.minute
    except:
        return 0


def minutes_to_time_str(minutes: int) -> str:
    """Convert minutes since midnight to time string like '09:00 AM'"""
    hours = minutes // 60
    mins = minutes % 60
    period = "AM" if hours < 12 else "PM"
    display_hours = hours if hours <= 12 else hours - 12
    if display_hours == 0:
        display_hours = 12
    return f"{display_hours:02d}:{mins:02d} {period}"


@app.get("/doctors/{doctor_id}/available-slots")
def get_available_slots(
    doctor_id: int,
    date: str,
    db: Session = Depends(get_db),
):
    """Get available time slots for a doctor on a specific date based on their availability schedule"""
    
    # Verify doctor exists
    doctor = db.query(models.Doctor).filter(
        models.Doctor.id == doctor_id,
        models.Doctor.is_available == True
    ).first()

    if not doctor:
        raise HTTPException(404, "Doctor not found or not available")

    # Parse the date
    try:
        appointment_date = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")

    # Get day of week (0=Monday, 6=Sunday)
    day_of_week = appointment_date.weekday()

    # Get doctor's availability for this day
    availability = db.query(models.DoctorAvailability).filter(
        models.DoctorAvailability.doctor_id == doctor_id,
        models.DoctorAvailability.day_of_week == day_of_week,
        models.DoctorAvailability.is_active == True
    ).first()

    if not availability:
        return {
            "doctor_id": doctor_id,
            "doctor_name": doctor.name,
            "date": date,
            "available_slots": [],
            "booked_slots": [],
            "message": f"Doctor is not available on {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][day_of_week]}"
        }

    # Generate time slots based on doctor's availability
    start_minutes = parse_time_to_minutes(availability.start_time)
    end_minutes = parse_time_to_minutes(availability.end_time)
    slot_duration = availability.slot_duration

    all_slots = []
    current_minutes = start_minutes
    
    while current_minutes + slot_duration <= end_minutes:
        slot_time = minutes_to_time_str(current_minutes)
        all_slots.append(slot_time)
        current_minutes += slot_duration

    # Get booked appointments for this doctor on this date
    booked_appointments = db.query(models.Appointment).filter(
        models.Appointment.doctor_id == doctor_id,
        models.Appointment.appointment_date == appointment_date,
        models.Appointment.status != "cancelled"
    ).all()

    booked_slots = [apt.appointment_time for apt in booked_appointments]

    # Hide slots in the past (earlier dates, or earlier times today)
    now = datetime.now()
    all_slots = [slot for slot in all_slots if slot_datetime(appointment_date, slot) > now]

    # Filter out booked slots
    available_slots = [slot for slot in all_slots if slot not in booked_slots]
    booked_slots = [slot for slot in booked_slots if slot in all_slots]

    return {
        "doctor_id": doctor_id,
        "doctor_name": doctor.name,
        "date": date,
        "day_of_week": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][day_of_week],
        "available_slots": available_slots,
        "booked_slots": booked_slots,
        "working_hours": f"{availability.start_time} - {availability.end_time}",
        "slot_duration": availability.slot_duration
    }


@app.get("/doctors/{doctor_id}/availability-summary")
def get_doctor_availability_summary(
    doctor_id: int,
    db: Session = Depends(get_db),
):
    """Get summary of doctor's weekly availability"""
    
    doctor = db.query(models.Doctor).filter(
        models.Doctor.id == doctor_id
    ).first()
    
    if not doctor:
        raise HTTPException(404, "Doctor not found")
    
    availability = db.query(models.DoctorAvailability).filter(
        models.DoctorAvailability.doctor_id == doctor_id,
        models.DoctorAvailability.is_active == True
    ).order_by(models.DoctorAvailability.day_of_week).all()
    
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    
    schedule = {}
    for day_num, day_name in enumerate(days):
        day_availability = next((a for a in availability if a.day_of_week == day_num), None)
        if day_availability:
            schedule[day_name] = {
                "available": True,
                "hours": f"{day_availability.start_time} - {day_availability.end_time}",
                "slot_duration": day_availability.slot_duration
            }
        else:
            schedule[day_name] = {
                "available": False,
                "hours": "Not available",
                "slot_duration": None
            }
    
    return {
        "doctor_id": doctor_id,
        "doctor_name": doctor.name,
        "schedule": schedule
    }


# ---------------------------------------------------------
# NURSE DASHBOARD ENDPOINTS
# ---------------------------------------------------------
@app.get("/nurse/profile")
def get_nurse_profile(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get nurse profile information"""
    if current_user.role != "nurse":
        raise HTTPException(403, "Access denied. Nurses only.")

    nurse = db.query(models.Nurse).filter(
        models.Nurse.user_id == current_user.id
    ).first()

    if not nurse:
        raise HTTPException(404, "Nurse profile not found")

    return {
        "id": nurse.id,
        "name": nurse.name,
        "email": nurse.email,
        "phone": nurse.phone,
        "license_number": nurse.license_number,
        "department": nurse.department,
        "shift": nurse.shift,
        "avatar": nurse.avatar,
        "is_available": nurse.is_available,
        "user_id": nurse.user_id
    }


@app.get("/nurse/patients/today")
def get_nurse_patients_today(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all patients with appointments today"""
    if current_user.role != "nurse":
        raise HTTPException(403, "Access denied. Nurses only.")

    today = datetime.now().date()

    # Get all appointments for today
    appointments = db.query(models.Appointment).filter(
        models.Appointment.appointment_date == today,
        models.Appointment.status.in_(["upcoming", "in_progress"])
    ).order_by(models.Appointment.appointment_time).all()

    return [
        {
            "id": a.id,
            "patient_name": a.user.full_name if a.user else "Unknown",
            "patient_id": a.user.student_id if a.user else "N/A",
            "doctor_name": a.doctor.name if a.doctor else "Unknown",
            "time": a.appointment_time,
            "type": a.type,
            "location": a.location,
            "status": a.status,
            "notes": a.notes
        }
        for a in appointments
    ]


@app.get("/nurse/patients/all")
def get_all_patients(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all registered patients (students)"""
    if current_user.role != "nurse":
        raise HTTPException(403, "Access denied. Nurses only.")

    # Get all users with role "student"
    patients = db.query(models.User).filter(
        models.User.role == "student",
        models.User.is_active == True
    ).order_by(models.User.full_name).all()

    return [
        {
            "id": p.id,
            "full_name": p.full_name,
            "student_id": p.student_id,
            "email": p.email,
            "phone": p.phone,
            "department": p.department,
            "major": p.major,
            "year_level": p.year_level
        }
        for p in patients
    ]


@app.get("/nurse/appointments/upcoming")
def get_upcoming_appointments_nurse(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all upcoming appointments"""
    if current_user.role != "nurse":
        raise HTTPException(403, "Access denied. Nurses only.")

    # Get all upcoming appointments
    appointments = db.query(models.Appointment).filter(
        models.Appointment.status == "upcoming",
        models.Appointment.appointment_date >= datetime.now().date()
    ).order_by(
        models.Appointment.appointment_date,
        models.Appointment.appointment_time
    ).limit(50).all()

    return [
        {
            "id": a.id,
            "patient_name": a.user.full_name if a.user else "Unknown",
            "patient_id": a.user.student_id if a.user else "N/A",
            "doctor_name": a.doctor.name if a.doctor else "Unknown",
            "date": a.appointment_date.isoformat(),
            "time": a.appointment_time,
            "type": a.type,
            "location": a.location,
            "status": a.status
        }
        for a in appointments
    ]


@app.get("/nurse/emergency-requests")
def get_emergency_requests(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get all active emergency requests"""
    if current_user.role != "nurse":
        raise HTTPException(403, "Access denied. Nurses only.")

    # Get active emergency requests
    emergency_requests = db.query(models.EmergencyRequest).filter(
        models.EmergencyRequest.status == "active"
    ).order_by(
        models.EmergencyRequest.priority.desc(),
        models.EmergencyRequest.created_at.desc()
    ).all()

    return [
        {
            "id": e.id,
            "patient_name": e.user.full_name if e.user else "Unknown",
            "patient_id": e.user.student_id if e.user else "N/A",
            "type": e.type,
            "description": e.description,
            "location": e.location,
            "priority": e.priority,
            "created_at": e.created_at.isoformat(),
            "status": e.status
        }
        for e in emergency_requests
    ]


@app.put("/nurse/emergency-requests/{request_id}/resolve")
def resolve_emergency_request(
    request_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark an emergency request as resolved"""
    if current_user.role != "nurse":
        raise HTTPException(403, "Access denied. Nurses only.")

    emergency_request = db.query(models.EmergencyRequest).filter(
        models.EmergencyRequest.id == request_id
    ).first()

    if not emergency_request:
        raise HTTPException(404, "Emergency request not found")

    emergency_request.status = "resolved"
    emergency_request.resolved_at = datetime.utcnow()

    db.commit()
    db.refresh(emergency_request)

    return {"message": "Emergency request resolved successfully"}


@app.get("/nurse/stats")
def get_nurse_stats(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get statistics for nurse dashboard"""
    if current_user.role != "nurse":
        raise HTTPException(403, "Access denied. Nurses only.")

    today = datetime.now().date()

    # Count today's appointments
    today_appointments = db.query(models.Appointment).filter(
        models.Appointment.appointment_date == today
    ).count()

    # Count active emergency requests
    active_emergencies = db.query(models.EmergencyRequest).filter(
        models.EmergencyRequest.status == "active"
    ).count()

    # Count total patients
    total_patients = db.query(models.User).filter(
        models.User.role == "student",
        models.User.is_active == True
    ).count()

    # Count upcoming appointments
    upcoming_appointments = db.query(models.Appointment).filter(
        models.Appointment.status == "upcoming",
        models.Appointment.appointment_date >= today
    ).count()

    return {
        "today_appointments": today_appointments,
        "active_emergencies": active_emergencies,
        "total_patients": total_patients,
        "upcoming_appointments": upcoming_appointments
    }


# ---------------------------------------------------------
# CHATBOT ENDPOINTS
# ---------------------------------------------------------
class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    user_context: Optional[dict] = None


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    model: Optional[str] = None
    mode: Optional[str] = None


@app.post("/chat/", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
    try:
        from chatbot import ai_reply

        conversation_id = request.conversation_id or f"conv_{datetime.utcnow().timestamp()}"
        
        result = ai_reply(
            message=request.message,
            conversation_id=conversation_id,
            user_context=request.user_context,
        )

        return ChatResponse(
            reply=result.get("reply", "I'm having trouble responding."),
            conversation_id=result.get("conversation_id", conversation_id),
            model=result.get("model"),
            mode=result.get("mode"),
        )

    except Exception as e:
        print("Chatbot error:", e)
        return ChatResponse(
            reply="I'm having technical difficulties.",
            conversation_id=request.conversation_id or "error",
            mode="error",
        )


# ---------------------------------------------------------
# PRESCRIPTIONS, REFERRALS, MEDICAL RECORDS (DOCTOR)
# ---------------------------------------------------------
@app.get("/users/students")
def get_all_students(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all students for doctor's dropdown menus"""
    if current_user.role not in ("doctor", "admin"):
        raise HTTPException(status_code=403, detail="Only doctors can access this")

    students = db.query(models.User).filter(
        models.User.role == "student", models.User.is_active == True
    ).order_by(models.User.full_name).all()

    return [
        {
            "id": student.id,
            "full_name": student.full_name,
            "student_id": student.student_id,
            "email": student.email
        }
        for student in students
    ]


@app.post("/prescriptions")
def create_prescription(
    prescription_data: PrescriptionCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new prescription"""
    if current_user.role != "doctor":
        raise HTTPException(status_code=403, detail="Only doctors can create prescriptions")

    doctor = db.query(models.Doctor).filter(models.Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    prescription = models.Prescription(
        patient_id=prescription_data.patient_id,
        doctor_id=doctor.id,
        medication=prescription_data.medication,
        dosage=prescription_data.dosage,
        frequency=prescription_data.frequency,
        duration=prescription_data.duration,
        instructions=prescription_data.instructions
    )

    db.add(prescription)
    db.commit()
    db.refresh(prescription)

    return {
        "message": "Prescription created successfully",
        "id": prescription.id
    }


@app.get("/doctor/prescriptions")
def get_doctor_prescriptions(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all prescriptions created by the current doctor"""
    if current_user.role != "doctor":
        raise HTTPException(status_code=403, detail="Only doctors can access this")

    doctor = db.query(models.Doctor).filter(models.Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    prescriptions = db.query(models.Prescription).filter(
        models.Prescription.doctor_id == doctor.id
    ).order_by(models.Prescription.created_at.desc()).all()

    result = []
    for presc in prescriptions:
        patient = db.query(models.User).filter(models.User.id == presc.patient_id).first()
        result.append({
            "id": presc.id,
            "medication": presc.medication,
            "dosage": presc.dosage,
            "frequency": presc.frequency,
            "duration": presc.duration,
            "instructions": presc.instructions,
            "status": presc.status,
            "patient_name": patient.full_name if patient else "Unknown",
            "patient_id": patient.student_id if patient else "Unknown",
            "created_at": presc.created_at.isoformat() if presc.created_at else None
        })

    return result


@app.delete("/prescriptions/{prescription_id}")
def delete_prescription(
    prescription_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a prescription - only the prescribing doctor can delete"""
    if current_user.role != "doctor":
        raise HTTPException(status_code=403, detail="Only doctors can delete prescriptions")

    doctor = db.query(models.Doctor).filter(models.Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    prescription = db.query(models.Prescription).filter(
        models.Prescription.id == prescription_id,
        models.Prescription.doctor_id == doctor.id  # Only allow deletion by prescribing doctor
    ).first()

    if not prescription:
        raise HTTPException(status_code=404, detail="Prescription not found or you don't have permission to delete it")

    db.delete(prescription)
    db.commit()

    return {"message": "Prescription deleted successfully"}


@app.post("/referrals")
def create_referral(
    referral_data: ReferralCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new referral"""
    if current_user.role != "doctor":
        raise HTTPException(status_code=403, detail="Only doctors can create referrals")

    doctor = db.query(models.Doctor).filter(models.Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    referral = models.Referral(
        patient_id=referral_data.patient_id,
        doctor_id=doctor.id,
        specialist_type=referral_data.specialist_type,
        reason=referral_data.reason,
        priority=referral_data.priority,
        notes=referral_data.notes
    )

    db.add(referral)
    db.commit()
    db.refresh(referral)

    return {
        "message": "Referral created successfully",
        "id": referral.id
    }


@app.post("/doctor/medical-records")
def add_medical_record_by_doctor(
    record_data: DoctorMedicalRecordCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Doctor adds a medical record for a patient"""
    if current_user.role != "doctor":
        raise HTTPException(status_code=403, detail="Only doctors can add medical records")

    # Verify patient exists
    patient = db.query(models.User).filter(models.User.id == record_data.patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    medical_entry = models.MedicalRecord(
        user_id=record_data.patient_id,
        type=record_data.type,
        name=record_data.name,
        severity=record_data.severity,
        description=record_data.description,
        is_active=True
    )

    db.add(medical_entry)
    db.commit()
    db.refresh(medical_entry)

    return {
        "message": "Medical record added successfully",
        "id": medical_entry.id
    }


@app.get("/my-prescriptions")
def get_my_prescriptions(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all prescriptions for the current student"""
    prescriptions = db.query(models.Prescription).filter(
        models.Prescription.patient_id == current_user.id
    ).order_by(models.Prescription.created_at.desc()).all()

    result = []
    for presc in prescriptions:
        doctor = db.query(models.Doctor).filter(models.Doctor.id == presc.doctor_id).first()
        result.append({
            "id": presc.id,
            "medication": presc.medication,
            "dosage": presc.dosage,
            "frequency": presc.frequency,
            "duration": presc.duration,
            "instructions": presc.instructions,
            "status": presc.status,
            "doctor_name": doctor.name if doctor else "Unknown",
            "created_at": presc.created_at.isoformat() if presc.created_at else None
        })

    return result


@app.get("/my-referrals")
def get_my_referrals(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all referrals for the current student"""
    referrals = db.query(models.Referral).filter(
        models.Referral.patient_id == current_user.id
    ).order_by(models.Referral.created_at.desc()).all()

    result = []
    for ref in referrals:
        doctor = db.query(models.Doctor).filter(models.Doctor.id == ref.doctor_id).first()
        result.append({
            "id": ref.id,
            "specialist_type": ref.specialist_type,
            "reason": ref.reason,
            "priority": ref.priority,
            "notes": ref.notes,
            "status": ref.status,
            "doctor_name": doctor.name if doctor else "Unknown",
            "created_at": ref.created_at.isoformat() if ref.created_at else None
        })

    return result


# ---------------------------------------------------------
# DOCTOR: REFERRALS AND PATIENT RECORDS
# ---------------------------------------------------------
@app.get("/doctor/referrals")
def get_doctor_referrals(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Referrals created by the current doctor"""
    require_role(current_user, "doctor")
    doctor = get_doctor_or_404(db, current_user)
    referrals = db.query(models.Referral).filter(
        models.Referral.doctor_id == doctor.id
    ).order_by(models.Referral.created_at.desc()).all()
    return [
        {
            "id": r.id,
            "specialist_type": r.specialist_type,
            "reason": r.reason,
            "priority": r.priority,
            "notes": r.notes,
            "status": r.status,
            "patient_name": r.patient.full_name if r.patient else "Unknown",
            "patient_id": r.patient.student_id if r.patient else "Unknown",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in referrals
    ]


class ReferralStatusUpdate(BaseModel):
    status: str


@app.put("/referrals/{referral_id}/status")
def update_referral_status(
    referral_id: int,
    update: ReferralStatusUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(current_user, "doctor")
    if update.status not in ("pending", "scheduled", "completed"):
        raise HTTPException(400, "Status must be pending, scheduled or completed")
    doctor = get_doctor_or_404(db, current_user)
    referral = db.query(models.Referral).filter(
        models.Referral.id == referral_id, models.Referral.doctor_id == doctor.id
    ).first()
    if not referral:
        raise HTTPException(404, "Referral not found")
    referral.status = update.status
    db.commit()
    return {"message": "Referral updated"}


@app.delete("/referrals/{referral_id}")
def delete_referral(
    referral_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Only the referring doctor can delete a referral"""
    require_role(current_user, "doctor")
    doctor = get_doctor_or_404(db, current_user)
    referral = db.query(models.Referral).filter(
        models.Referral.id == referral_id, models.Referral.doctor_id == doctor.id
    ).first()
    if not referral:
        raise HTTPException(404, "Referral not found")
    db.delete(referral)
    db.commit()
    return {"message": "Referral deleted"}


@app.get("/doctor/patients/{patient_user_id}/records")
def get_patient_records(
    patient_user_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """A doctor sees a patient's medical information, prescriptions and visits"""
    require_role(current_user, "doctor")
    patient = db.query(models.User).filter(
        models.User.id == patient_user_id, models.User.role == "student"
    ).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    records = db.query(models.MedicalRecord).filter(
        models.MedicalRecord.user_id == patient.id, models.MedicalRecord.is_active == True
    ).all()
    prescriptions = db.query(models.Prescription).filter(
        models.Prescription.patient_id == patient.id
    ).order_by(models.Prescription.created_at.desc()).all()
    visits = db.query(models.Visit).filter(
        models.Visit.user_id == patient.id
    ).order_by(models.Visit.visit_date.desc()).limit(5).all()
    return {
        "patient": {"id": patient.id, "full_name": patient.full_name, "student_id": patient.student_id,
                    "email": patient.email, "phone": patient.phone},
        "records": [{"id": r.id, "type": r.type, "name": r.name, "severity": r.severity,
                     "description": r.description} for r in records],
        "prescriptions": [{"medication": p.medication, "dosage": p.dosage, "frequency": p.frequency,
                           "status": p.status, "doctor_name": p.doctor.name if p.doctor else "Unknown"}
                          for p in prescriptions],
        "visits": [{"date": v.visit_date.isoformat(), "diagnosis": v.diagnosis,
                    "doctor_name": v.doctor.name if v.doctor else "Unknown"} for v in visits],
    }


# ---------------------------------------------------------
# ADMIN
# ---------------------------------------------------------
class AdminStatusUpdate(BaseModel):
    is_active: bool


@app.get("/admin/stats")
def admin_stats(current_user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_role(current_user, "admin")
    today = datetime.now().date()
    count = lambda q: q.count()
    return {
        "students": count(db.query(models.User).filter(models.User.role == "student")),
        "doctors": count(db.query(models.User).filter(models.User.role == "doctor")),
        "nurses": count(db.query(models.User).filter(models.User.role == "nurse")),
        "inactive_users": count(db.query(models.User).filter(models.User.is_active == False)),
        "upcoming_appointments": count(db.query(models.Appointment).filter(
            models.Appointment.status == "upcoming", models.Appointment.appointment_date >= today)),
        "appointments_today": count(db.query(models.Appointment).filter(
            models.Appointment.appointment_date == today, models.Appointment.status != "cancelled")),
        "active_emergencies": count(db.query(models.EmergencyRequest).filter(
            models.EmergencyRequest.status == "active")),
        "prescriptions": count(db.query(models.Prescription)),
        "referrals": count(db.query(models.Referral)),
    }


@app.get("/admin/users")
def admin_list_users(
    role: Optional[str] = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_role(current_user, "admin")
    query = db.query(models.User)
    if role:
        query = query.filter(models.User.role == role)
    users = query.order_by(models.User.role, models.User.full_name).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "full_name": u.full_name,
            "email": u.email,
            "student_id": u.student_id,
            "role": u.role,
            "department": u.department,
            "major": u.major,
            "is_active": u.is_active is not False,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


@app.post("/admin/users")
def admin_create_user(
    user: UserRegister,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin creates a student, doctor or nurse account (same rules as registration)"""
    require_role(current_user, "admin")
    result = register(user, db)
    return {"message": f"{user.role.capitalize()} account created", "user": result["user"]}


@app.put("/admin/users/{user_id}/status")
def admin_set_user_status(
    user_id: int,
    update: AdminStatusUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_role(current_user, "admin")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == current_user.id:
        raise HTTPException(400, "You cannot deactivate your own account")
    user.is_active = update.is_active
    # A deactivated doctor no longer appears for booking
    if user.doctor_profile:
        user.doctor_profile.is_available = update.is_active
    db.commit()
    return {"message": "Account activated" if update.is_active else "Account deactivated"}


@app.get("/admin/appointments")
def admin_list_appointments(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_role(current_user, "admin")
    appointments = db.query(models.Appointment).order_by(
        models.Appointment.appointment_date.desc()
    ).limit(200).all()
    return [
        {
            "id": a.id,
            "patient_name": a.user.full_name if a.user else "Unknown",
            "doctor_name": a.doctor.name if a.doctor else "Unknown",
            "date": a.appointment_date.isoformat() if a.appointment_date else None,
            "time": a.appointment_time,
            "type": a.type,
            "status": a.status,
        }
        for a in appointments
    ]


@app.delete("/admin/appointments/{appointment_id}")
def admin_cancel_appointment(
    appointment_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_role(current_user, "admin")
    appointment = db.query(models.Appointment).filter(models.Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(404, "Appointment not found")
    appointment.status = "cancelled"
    appointment.can_reschedule = False
    db.commit()
    return {"message": "Appointment cancelled"}


# ---------------------------------------------------------
# STARTUP EVENT - Initialize Database
# ---------------------------------------------------------
@app.on_event("startup")
def startup_event():
    """Initialize database on startup"""
    from database import init_db
    init_db()


# ---------------------------------------------------------
# RUN SERVER
# ---------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    print("???? Starting CareConnect API Server...")
    print("???? Server: http://127.0.0.1:8000")
    print("???? Docs: http://127.0.0.1:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)

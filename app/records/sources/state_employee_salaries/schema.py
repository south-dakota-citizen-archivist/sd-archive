"""A state employee's position and pay, as open.sd.gov's salary lookup shows it."""

from pydantic import BaseModel


class Salary(BaseModel):
    active: bool  # False: a former employee, shown with their final wage
    position_number: str | None  # None for former employees
    last_name: str
    first_name: str
    middle_initial: str | None
    agency_code: str | None  # "DOC"
    agency: str | None  # "Department of Corrections"
    title: str | None  # None for former employees ("Inactive – final wage shown")
    pay: str  # as shown: "$25.60"
    pay_amount: float | None
    pay_basis: str  # Hourly, Annually, Daily…


VERSION = "1"

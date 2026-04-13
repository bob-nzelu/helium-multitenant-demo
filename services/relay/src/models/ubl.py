"""
UBL Invoice Model — Abbey Mortgage tenant format.

Pydantic model matching the UBL structure used by Abbey for both
inbound (invoices.json) and outbound (simulator-generated) invoices.

The Relay uses this model for validation when tenant.format_type == "ubl".
"""

from typing import List, Optional
from pydantic import BaseModel, Field, validator


class PostalAddress(BaseModel):
    streetName: Optional[str] = Field(None, alias="street_name")
    cityName: Optional[str] = Field(None, alias="city_name")
    country: Optional[str] = None

    class Config:
        populate_by_name = True


class Party(BaseModel):
    partyName: str = Field(..., alias="party_name")
    tin: Optional[str] = None
    email: Optional[str] = None
    postalAddress: Optional[PostalAddress] = Field(None, alias="postal_address")

    class Config:
        populate_by_name = True


class ItemInfo(BaseModel):
    name: str


class PriceInfo(BaseModel):
    priceAmount: float = Field(..., alias="price_amount")

    class Config:
        populate_by_name = True


class InvoiceLine(BaseModel):
    hsnCode: Optional[str] = Field(None, alias="hsn_code")
    invoicedQuantity: float = Field(1, alias="invoiced_quantity")
    lineExtensionAmount: float = Field(0, alias="line_extension_amount")
    item: ItemInfo
    price: PriceInfo

    class Config:
        populate_by_name = True


class TaxCategory(BaseModel):
    id: Optional[str] = None
    percent: float = 7.5


class TaxSubtotal(BaseModel):
    taxableAmount: float = Field(0, alias="taxable_amount")
    taxAmount: float = Field(0, alias="tax_amount")
    taxCategory: TaxCategory = Field(default_factory=TaxCategory, alias="tax_category")

    class Config:
        populate_by_name = True


class TaxTotal(BaseModel):
    taxAmount: float = Field(0, alias="tax_amount")
    taxSubtotal: List[TaxSubtotal] = Field(default_factory=list, alias="tax_subtotal")

    class Config:
        populate_by_name = True


class LegalMonetaryTotal(BaseModel):
    lineExtensionAmount: float = Field(0, alias="line_extension_amount")
    taxExclusiveAmount: float = Field(0, alias="tax_exclusive_amount")
    taxInclusiveAmount: float = Field(0, alias="tax_inclusive_amount")
    payableAmount: float = Field(0, alias="payable_amount")

    class Config:
        populate_by_name = True


class UBLInvoice(BaseModel):
    """
    UBL-format invoice as used by Abbey Mortgage.
    Matches the structure in invoices.json and possible_account_receivables.xlsx.
    """
    businessId: Optional[str] = Field(None, alias="business_id")
    irn: Optional[str] = None
    issueDate: str = Field(..., alias="issue_date")
    invoiceTypeCode: str = Field(..., alias="invoice_type_code")
    documentCurrencyCode: str = Field("NGN", alias="document_currency_code")
    taxCurrencyCode: str = Field("NGN", alias="tax_currency_code")

    accountingSupplierParty: Party = Field(..., alias="accounting_supplier_party")
    accountingCustomerParty: Optional[Party] = Field(None, alias="accounting_customer_party")

    invoiceLine: List[InvoiceLine] = Field(..., alias="invoice_line")
    taxTotal: List[TaxTotal] = Field(default_factory=list, alias="tax_total")
    legalMonetaryTotal: Optional[LegalMonetaryTotal] = Field(None, alias="legal_monetary_total")

    class Config:
        populate_by_name = True

    def compute_totals(self) -> dict:
        """
        Compute subtotal, tax, and total from line items and tax totals.
        Handles the common case where legalMonetaryTotal amounts are 0.
        """
        subtotal = sum(
            line.price.priceAmount * line.invoicedQuantity
            for line in self.invoiceLine
        )

        tax_amount = 0.0
        if self.taxTotal:
            tax_amount = sum(t.taxAmount for t in self.taxTotal)

        if tax_amount == 0.0 and subtotal > 0:
            for tt in self.taxTotal:
                for sub in tt.taxSubtotal:
                    if sub.taxCategory.percent > 0:
                        tax_amount = round(subtotal * sub.taxCategory.percent / 100, 2)
                        break
                if tax_amount > 0:
                    break

        total = subtotal + tax_amount

        return {
            "subtotal": round(subtotal, 2),
            "tax_amount": round(tax_amount, 2),
            "total_amount": round(total, 2),
        }

    def extract_transaction_id(self) -> str:
        """Derive a unique transaction_id from the invoice."""
        return self.invoiceTypeCode or self.businessId or "UNKNOWN"

    def extract_flat_record(self) -> dict:
        """
        Extract a flat record for Relay processing (IRN/QR generation, storage).
        Maps UBL fields to the flat schema used by BatchRecordResult.
        """
        totals = self.compute_totals()
        txn_id = self.extract_transaction_id()

        description_parts = [line.item.name for line in self.invoiceLine]
        description = "; ".join(description_parts)

        return {
            "transaction_id": txn_id,
            "fee_amount": totals["subtotal"],
            "vat_amount": totals["tax_amount"],
            "total_amount": totals["total_amount"],
            "description": description,
            "issue_date": self.issueDate,
            "currency": self.documentCurrencyCode,
            "supplier_name": self.accountingSupplierParty.partyName,
            "supplier_tin": self.accountingSupplierParty.tin,
            "buyer_name": (
                self.accountingCustomerParty.partyName
                if self.accountingCustomerParty else None
            ),
            "line_items": [
                {
                    "description": line.item.name,
                    "quantity": line.invoicedQuantity,
                    "unit_price": line.price.priceAmount,
                    "hsn_code": line.hsnCode,
                }
                for line in self.invoiceLine
            ],
        }

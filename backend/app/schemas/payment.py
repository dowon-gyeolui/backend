from pydantic import BaseModel


class OrderCreate(BaseModel):
    product_id: str  # "STAR-001" ~ "STAR-004"


class OrderResponse(BaseModel):
    """토스 결제창 호출에 필요한 값. 금액은 서버가 확정한 값이다."""

    order_id: str
    product_id: str
    amount: int
    star_amount: int
    order_name: str  # 결제창에 표시 — "ZAMI 스타 50개"


class ConfirmRequest(BaseModel):
    """토스 successUrl 리다이렉트의 paymentKey/orderId/amount 를 그대로 전달."""

    payment_key: str
    order_id: str
    amount: int


class BalanceResponse(BaseModel):
    star_balance: int

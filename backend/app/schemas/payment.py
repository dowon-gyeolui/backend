"""스타 충전 주문 생성/결제 확인 스키마."""

from pydantic import BaseModel


class OrderCreate(BaseModel):
    product_id: str


class OrderResponse(BaseModel):
    """토스 결제창 호출에 필요한 값. 금액은 서버가 확정한 값이다."""

    order_id: str
    product_id: str
    amount: int
    star_amount: int
    order_name: str


class ConfirmRequest(BaseModel):
    """토스 successUrl 리다이렉트의 paymentKey/orderId/amount 를 그대로 전달."""

    payment_key: str
    order_id: str
    amount: int


class BalanceResponse(BaseModel):
    star_balance: int


class ReconcileResponse(BaseModel):
    """미확정 주문 정리 결과. `credited_stars` 가 0 보다 크면 화면에 알린다."""

    star_balance: int
    credited_stars: int
    settled_orders: int

def wallet_balance(request):
    if not request.user.is_authenticated:
        return {"nav_wallet_balance": None, "nav_wallet_available": None}
    from accounts.models import Wallet

    w, _ = Wallet.objects.get_or_create(user=request.user)
    return {
        "nav_wallet_balance": w.balance,
        "nav_wallet_available": w.available_balance,
    }

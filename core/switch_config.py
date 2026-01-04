# IP prefix for each network type
IP_PREFIX = {
    "ATN": "10.254.252",   # Always Huawei
    "COLO": "10.254.254",  # Could be Huawei or Cisco
}

# Location configs with their last IP octet and device type per network
SWITCH_CONFIG = {
    # Location: { ip_octet, device_type per network }
    "BOYOLANGU": {"ip": 1, "is_huawei": True},
    "BEJI": {"ip": 9, "is_huawei": True},
    "DURENAN": {"ip": 5, "is_huawei": True},
    "KALIDAWIR": {"ip": 3, "is_huawei": True},
    "KAUMAN": {"ip": 4, "is_huawei": True},
    "KEDIRI": {"ip": 8, "is_huawei": True},
    "CAMPUR BARU": {"ip": 15, "is_huawei": True},
    "BLITAR": {"ip": 2, "is_huawei": True},
    "GANDUSARI": {"ip": 11, "is_huawei": True},
}

COMMAND_TEMPLATE = {
    "cek_description": {
        "huawei": ["display interface description"],
        "cisco": ["show interface description"]
    },
    "cek_interface": {
        "huawei": ["display interface {interface}"],
        "cisco": ["show interface {interface}"]
    }
}


def get_switch_connection(network_type: str, location: str) -> dict | None:
    """
    Get switch connection info.
    
    Args:
        network_type: "ATN" or "COLO"
        location: e.g., "BOYOLANGU", "BEJI"
    
    Returns:
        {"ip": "10.254.252.1", "is_huawei": True}
    
    Example:
        get_switch_connection("ATN", "BOYOLANGU")
        -> {"ip": "10.254.252.1", "is_huawei": True}
        
        get_switch_connection("COLO", "CAMPUR BARU")  
        -> {"ip": "10.254.254.7", "is_huawei": True}
    """
    network_upper = network_type.upper()
    location_upper = location.upper()
    
    # Get prefix
    prefix = IP_PREFIX.get(network_upper)
    if not prefix:
        return None
    
    # Get location config
    config = SWITCH_CONFIG.get(location_upper)
    if not config:
        return None
    
    # Build full IP
    full_ip = f"{prefix}.{config['ip']}"
    
    # Determine if Huawei based on network type
    if network_upper == "ATN":
        is_huawei = config.get("atn_huawei", True)  # ATN is always Huawei
    else:  # COLO
        is_huawei = config.get("colo_huawei", False)
    
    return {
        "ip": full_ip,
        "is_huawei": is_huawei
    }

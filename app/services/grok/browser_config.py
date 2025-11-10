"""浏览器指纹配置 - 集中管理所有浏览器相关配置"""

# Windows Chrome 142 浏览器指纹配置
BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
BROWSER_SEC_CH_UA = '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"'
BROWSER_SEC_CH_UA_PLATFORM = '"Windows"'
BROWSER_SEC_CH_UA_MOBILE = "?0"

# curl_cffi impersonate 配置
CURL_IMPERSONATE = "chrome"

# curl_cffi TLS 指纹配置
CURL_JA3 = "771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,17613-51-10-35-65037-27-16-11-5-45-18-65281-0-13-43-23,4588-29-23-24,0"
CURL_AKAMAI = "1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p"

# Playwright 浏览器配置
PLAYWRIGHT_CHANNEL = "chrome"
PLAYWRIGHT_VIEWPORT = {"width": 1920, "height": 1080}


def get_browser_fingerprint() -> dict:
    """获取浏览器指纹配置字典"""
    return {
        "browser_user_agent": BROWSER_USER_AGENT,
        "browser_sec_ch_ua": BROWSER_SEC_CH_UA,
        "browser_sec_ch_ua_platform": BROWSER_SEC_CH_UA_PLATFORM,
        "browser_sec_ch_ua_mobile": BROWSER_SEC_CH_UA_MOBILE
    }


def get_playwright_headers() -> dict:
    """获取 Playwright 的 extra_http_headers"""
    return {
        "sec-ch-ua": BROWSER_SEC_CH_UA,
        "sec-ch-ua-mobile": BROWSER_SEC_CH_UA_MOBILE,
        "sec-ch-ua-platform": BROWSER_SEC_CH_UA_PLATFORM
    }

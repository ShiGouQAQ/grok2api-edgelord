"""浏览器指纹集中配置

Chrome 148 自定义指纹（来源：tls.browserleaks.com 抓包）
"""

# Windows Chrome 148
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)
BROWSER_SEC_CH_UA = '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"'
BROWSER_SEC_CH_UA_PLATFORM = '"Windows"'
BROWSER_SEC_CH_UA_MOBILE = "?0"
PLAYWRIGHT_CHANNEL = "chrome"
PLAYWRIGHT_VIEWPORT = {"width": 1920, "height": 1080}

# Chrome 148 JA3 指纹（JA3N 格式，sorted extensions）
# 来源：tls.browserleaks.com 抓包
BROWSER_JA3 = (
    "771,"
    "4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,"
    "0-5-10-11-13-16-18-23-27-35-43-45-51-17613-65037-65281,"
    "4588-29-23-24,"
    "0"
)

# Chrome 148 Akamai HTTP/2 指纹
BROWSER_AKAMAI = "1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p"

# 额外指纹参数
BROWSER_EXTRA_FP = {
    "tls_signature_algorithms": [
        "ecdsa_secp256r1_sha256",
        "rsa_pss_rsae_sha256",
        "rsa_pkcs1_sha256",
        "ecdsa_secp384r1_sha384",
        "rsa_pss_rsae_sha384",
        "rsa_pkcs1_sha384",
        "rsa_pss_rsae_sha512",
        "rsa_pkcs1_sha512",
    ],
    "tls_permute_extensions": True,
    "tls_cert_compression": "brotli",
}

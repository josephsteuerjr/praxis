use base64::Engine;
use serde::Deserialize;
use serde::Serialize;
use thiserror::Error;

#[derive(Deserialize, Serialize, Clone, Debug, PartialEq, Default)]
pub struct TokenData {
    /// Flat info parsed from the JWT in auth.json.
    #[serde(deserialize_with = "deserialize_id_token")]
    pub id_token: IdTokenInfo,

    /// This is a JWT.
    pub access_token: String,

    pub refresh_token: String,

    pub account_id: Option<String>,
}

impl TokenData {
    /// Returns true if this is a plan that should use the traditional
    /// "metered" billing via an API key.
    pub(crate) fn is_plan_that_should_use_api_key(&self) -> bool {
        self.id_token
            .chatgpt_plan_type
            .as_ref()
            .is_none_or(|plan| plan.is_plan_that_should_use_api_key())
    }
}

/// Flat subset of useful claims in id_token from auth.json.
///
/// ⚠ `Deserialize` здесь не для красоты симметрии — см. `deserialize_id_token` ниже:
/// без него испорченный прежней записью файл не читается вовсе.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct IdTokenInfo {
    pub email: Option<String>,
    /// The ChatGPT subscription plan type
    /// (e.g., "free", "plus", "pro", "business", "enterprise", "edu").
    /// (Note: ae has not verified that those are the exact values.)
    pub(crate) chatgpt_plan_type: Option<PlanType>,
}

impl IdTokenInfo {
    #[allow(dead_code)]
    pub fn get_chatgpt_plan_type(&self) -> Option<String> {
        self.chatgpt_plan_type.as_ref().map(|t| match t {
            PlanType::Known(plan) => format!("{plan:?}"),
            PlanType::Unknown(s) => s.clone(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub(crate) enum PlanType {
    Known(KnownPlan),
    Unknown(String),
}

impl PlanType {
    fn is_plan_that_should_use_api_key(&self) -> bool {
        match self {
            Self::Known(known) => {
                use KnownPlan::*;
                !matches!(known, Free | Plus | Pro | Team)
            }
            Self::Unknown(_) => {
                // Unknown plans should use the API key.
                true
            }
        }
    }

    #[allow(dead_code)]
    pub fn as_string(&self) -> String {
        match self {
            Self::Known(known) => format!("{known:?}").to_lowercase(),
            Self::Unknown(s) => s.clone(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub(crate) enum KnownPlan {
    Free,
    Plus,
    Pro,
    Team,
    Business,
    Enterprise,
    Edu,
}

#[derive(Deserialize)]
struct IdClaims {
    #[serde(default)]
    email: Option<String>,
    #[serde(rename = "https://api.openai.com/auth", default)]
    auth: Option<AuthClaims>,
}

#[derive(Deserialize)]
struct AuthClaims {
    #[serde(default)]
    chatgpt_plan_type: Option<PlanType>,
}

#[derive(Debug, Error)]
pub enum IdTokenInfoError {
    #[error("invalid ID token format")]
    InvalidFormat,
    #[error(transparent)]
    Base64(#[from] base64::DecodeError),
    #[error(transparent)]
    Json(#[from] serde_json::Error),
}

/// Seconds since the epoch at which this JWT stops being accepted upstream.
///
/// ⚠ WHY THIS EXISTS.  `last_refresh` in auth.json says when we last talked to the token
/// endpoint; it says nothing about how long the issued access token lives.  Those two
/// drifted apart: the refresh gate waited 28 days while the access token lived ~10.  On
/// 2026-08-10 the secondary slot's token expired at 09:34 UTC, the relay kept presenting it,
/// upstream answered 401 to every single call, and the whole companion went mute for hours.
/// The expiry is written inside the token itself — so read it instead of guessing.
pub(crate) fn jwt_expires_at(token: &str) -> Option<i64> {
    #[derive(Deserialize)]
    struct ExpClaim {
        #[serde(default)]
        exp: Option<i64>,
    }

    let mut parts = token.split('.');
    let payload_b64 = match (parts.next(), parts.next(), parts.next()) {
        (Some(h), Some(p), Some(s)) if !h.is_empty() && !p.is_empty() && !s.is_empty() => p,
        _ => return None,
    };
    let payload_bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(payload_b64)
        .ok()?;
    serde_json::from_slice::<ExpClaim>(&payload_bytes)
        .ok()
        .and_then(|claims| claims.exp)
}

impl TokenData {
    /// True when the access token is already dead or dies within `skew_secs`.
    ///
    /// An opaque (non-JWT) token has no readable expiry; treat it as fresh so this never
    /// turns into a refresh storm against an upstream that never told us anything.
    pub(crate) fn access_token_expiring(&self, now_secs: i64, skew_secs: i64) -> bool {
        match jwt_expires_at(&self.access_token) {
            Some(exp) => exp <= now_secs.saturating_add(skew_secs),
            None => false,
        }
    }
}

pub(crate) fn parse_id_token(id_token: &str) -> Result<IdTokenInfo, IdTokenInfoError> {
    // JWT format: header.payload.signature
    let mut parts = id_token.split('.');
    let (_header_b64, payload_b64, _sig_b64) = match (parts.next(), parts.next(), parts.next()) {
        (Some(h), Some(p), Some(s)) if !h.is_empty() && !p.is_empty() && !s.is_empty() => (h, p, s),
        _ => return Err(IdTokenInfoError::InvalidFormat),
    };

    let payload_bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD.decode(payload_b64)?;
    let claims: IdClaims = serde_json::from_slice(&payload_bytes)?;

    Ok(IdTokenInfo {
        email: claims.email,
        chatgpt_plan_type: claims.auth.and_then(|a| a.chatgpt_plan_type),
    })
}

/// Читаем `id_token` в ОБЕИХ формах — и вот почему.
///
/// ⚠ ЖИВОЙ СЛУЧАЙ 13.08.2026.  `update_tokens` клал сюда РАЗОБРАННУЮ структуру
/// (`{"email":…,"chatgpt_plan_type":…}`), а этот читатель требовал строку.  То есть рефреш
/// записывал файл в форме, которую сам же не умеет прочесть.  `lease_slot` перечитывает
/// auth.json на КАЖДУЮ аренду, поэтому порча всплывает первым же следующим запросом: слот
/// `primary` умер, компаньон онемел на полтора часа.
///
/// Запись починена ниже по течению (`write_auth_json` кладёт сырой JWT).  Но файлы, уже
/// испорченные прежней записью, лежат на диске: если читать только строку, починка записи
/// не воскрешает то, что она сломала.  Поэтому объект принимается тоже — как след, а не
/// как равноправная форма.
fn deserialize_id_token<'de, D>(deserializer: D) -> Result<IdTokenInfo, D::Error>
where
    D: serde::Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum StoredIdToken {
        /// Единственная форма, которую пишет апстрим — и теперь снова пишем мы.
        Jwt(String),
        /// След прежней порчи: разобранные поля вместо самого токена.
        Parsed(IdTokenInfo),
    }

    match StoredIdToken::deserialize(deserializer)? {
        StoredIdToken::Jwt(jwt) => parse_id_token(&jwt).map_err(serde::de::Error::custom),
        StoredIdToken::Parsed(info) => Ok(info),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Serialize;

    #[test]
    #[expect(clippy::expect_used, clippy::unwrap_used)]
    fn id_token_info_parses_email_and_plan() {
        // Build a fake JWT with a URL-safe base64 payload containing email and plan.
        #[derive(Serialize)]
        struct Header {
            alg: &'static str,
            typ: &'static str,
        }
        let header = Header {
            alg: "none",
            typ: "JWT",
        };
        let payload = serde_json::json!({
            "email": "user@example.com",
            "https://api.openai.com/auth": {
                "chatgpt_plan_type": "pro"
            }
        });

        fn b64url_no_pad(bytes: &[u8]) -> String {
            base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
        }

        let header_b64 = b64url_no_pad(&serde_json::to_vec(&header).unwrap());
        let payload_b64 = b64url_no_pad(&serde_json::to_vec(&payload).unwrap());
        let signature_b64 = b64url_no_pad(b"sig");
        let fake_jwt = format!("{header_b64}.{payload_b64}.{signature_b64}");

        let info = parse_id_token(&fake_jwt).expect("should parse");
        assert_eq!(info.email.as_deref(), Some("user@example.com"));
        assert_eq!(
            info.chatgpt_plan_type,
            Some(PlanType::Known(KnownPlan::Pro))
        );
    }

    fn jwt_with_exp(exp: Option<i64>) -> String {
        fn b64url_no_pad(bytes: &[u8]) -> String {
            base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
        }
        let payload = match exp {
            Some(value) => serde_json::json!({ "exp": value }),
            None => serde_json::json!({}),
        };
        #[expect(clippy::unwrap_used)]
        let payload_b64 = b64url_no_pad(&serde_json::to_vec(&payload).unwrap());
        format!(
            "{}.{}.{}",
            b64url_no_pad(b"{}"),
            payload_b64,
            b64url_no_pad(b"sig")
        )
    }

    fn token_with(access: String) -> TokenData {
        TokenData {
            id_token: IdTokenInfo::default(),
            access_token: access,
            refresh_token: "r".to_string(),
            account_id: Some("acct".to_string()),
        }
    }

    /// ЗАМЕР ПРОДА 10.08.2026.  Токен слота `secondary`: выдан 31.07 09:34, истёк 10.08
    /// 09:34 — ровно десять дней.  Гейт рефреша ждал двадцать восьмого дня, поэтому
    /// протухший токен предъявлялся апстриму как ни в чём не бывало, и тот отвечал 401 на
    /// каждый вызов.  Срок живёт ВНУТРИ токена; читаем его, а не календарь.
    #[test]
    fn an_expired_access_token_is_seen_before_upstream_says_401() {
        let issued = 1_785_490_483_i64; // 2026-07-31T09:34:43Z
        let expires = 1_786_354_483_i64; // 2026-08-10T09:34:43Z
        let token = token_with(jwt_with_exp(Some(expires)));

        assert!(
            !token.access_token_expiring(issued, 3600),
            "свежий токен не трогаем"
        );
        assert!(
            !token.access_token_expiring(expires - 7200, 3600),
            "за два часа до срока запаса ещё хватает"
        );
        assert!(
            token.access_token_expiring(expires - 600, 3600),
            "за десять минут до срока обязаны обновиться заранее"
        );
        assert!(
            token.access_token_expiring(expires + 1, 3600),
            "истёкший токен обязан читаться как истёкший"
        );
    }

    /// ЖИВОЙ СЛУЧАЙ 13.08.2026: слот `primary` после рефреша перестал читаться.
    ///
    /// Файл на диске выглядел так, как его записал наш же `update_tokens`, — с объектом
    /// вместо токена. Читатель требовал строку, `AccountRouter::load` падал, компаньон
    /// молчал полтора часа. Здесь охраняется именно то, что мы умеем поднять свой же
    /// испорченный файл, а не только правильный.
    #[test]
    #[expect(clippy::expect_used)]
    fn a_file_broken_by_our_own_refresh_still_loads() {
        let broken = serde_json::json!({
            "id_token": {"email": "user@example.com", "chatgpt_plan_type": "pro"},
            "access_token": "a",
            "refresh_token": "r",
            "account_id": "acct",
        });
        let tokens: TokenData =
            serde_json::from_value(broken).expect("испорченный файл обязан читаться");
        assert_eq!(tokens.id_token.email.as_deref(), Some("user@example.com"));
        assert_eq!(
            tokens.id_token.chatgpt_plan_type,
            Some(PlanType::Known(KnownPlan::Pro)),
            "план потерян — реле решит, что это метрированный аккаунт, и уйдёт в API-ключ"
        );
    }

    /// Правильная форма — по-прежнему сам JWT, и он по-прежнему разбирается.
    #[test]
    #[expect(clippy::expect_used, clippy::unwrap_used)]
    fn the_upstream_shape_is_still_the_shape_we_read() {
        let jwt = {
            fn b64(bytes: &[u8]) -> String {
                base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
            }
            let payload = serde_json::json!({
                "email": "user@example.com",
                "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
            });
            format!(
                "{}.{}.{}",
                b64(b"{}"),
                b64(&serde_json::to_vec(&payload).unwrap()),
                b64(b"sig")
            )
        };
        let value = serde_json::json!({
            "id_token": jwt,
            "access_token": "a",
            "refresh_token": "r",
            "account_id": "acct",
        });
        let tokens: TokenData =
            serde_json::from_value(value).expect("нормальный файл обязан читаться");
        assert_eq!(tokens.id_token.email.as_deref(), Some("user@example.com"));
    }

    /// А вот мусор обязан оставаться мусором: строка, не похожая на JWT, — это ошибка,
    /// а не молчаливый пустой `IdTokenInfo`. Иначе реле поедет без плана и без почты.
    #[test]
    fn a_string_that_is_not_a_jwt_is_still_an_error() {
        let value = serde_json::json!({
            "id_token": "not-a-jwt",
            "access_token": "a",
            "refresh_token": "r",
            "account_id": "acct",
        });
        assert!(serde_json::from_value::<TokenData>(value).is_err());
    }

    #[test]
    fn a_token_without_a_readable_expiry_is_left_alone() {
        // Иначе непрозрачный токен превратился бы в бесконечный шторм рефрешей против
        // апстрима, который нам ничего про срок не говорил.
        assert!(!token_with("opaque-not-a-jwt".to_string()).access_token_expiring(0, 3600));
        assert!(!token_with(jwt_with_exp(None)).access_token_expiring(0, 3600));
        assert!(jwt_expires_at("a.b").is_none());
        assert!(jwt_expires_at("").is_none());
    }
}

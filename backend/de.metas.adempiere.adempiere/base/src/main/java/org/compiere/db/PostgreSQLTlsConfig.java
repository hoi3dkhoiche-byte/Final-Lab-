package org.compiere.db;

import java.io.File;
import java.io.UnsupportedEncodingException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/**
 * Optional pgJDBC TLS parameters for the legacy ERP datasource.
 * Local deployments keep their existing behavior when both options are absent.
 */
final class PostgreSQLTlsConfig
{
	static final String SSL_MODE_PROPERTY = "metasfresh.db.sslmode";
	static final String SSL_ROOT_CERT_PROPERTY = "metasfresh.db.sslrootcert";

	private PostgreSQLTlsConfig()
	{
	}

	static String appendToUrl(final String jdbcUrl, final String configuredMode, final String configuredRootCert)
	{
		final String mode = configuredMode == null ? "" : configuredMode.trim();
		final String rootCert = configuredRootCert == null ? "" : configuredRootCert.trim();
		if (mode.isEmpty())
		{
			if (!rootCert.isEmpty())
			{
				throw new IllegalArgumentException("metasfresh.db.sslrootcert requires an explicit metasfresh.db.sslmode");
			}
			return jdbcUrl;
		}

		if (!"verify-full".equals(mode) && !"verify-ca".equals(mode))
		{
			throw new IllegalArgumentException("metasfresh.db.sslmode must be verify-full or verify-ca");
		}
		if (rootCert.isEmpty())
		{
			throw new IllegalArgumentException("Verified PostgreSQL TLS requires metasfresh.db.sslrootcert");
		}
		// The Unix check also keeps the Linux container contract testable on Windows.
		if (!rootCert.startsWith("/") && !new File(rootCert).isAbsolute())
		{
			throw new IllegalArgumentException("metasfresh.db.sslrootcert must be an absolute file path");
		}

		try
		{
			final String encodedRootCert = URLEncoder.encode(rootCert, StandardCharsets.UTF_8.name());
			return jdbcUrl + "&sslmode=" + mode + "&sslrootcert=" + encodedRootCert;
		}
		catch (final UnsupportedEncodingException ex)
		{
			throw new IllegalStateException("UTF-8 encoding is unavailable", ex);
		}
	}
}

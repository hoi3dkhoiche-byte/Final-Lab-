package org.compiere.db;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.parallel.ResourceLock;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ResourceLock("metasfresh.db.TLS")
class PostgreSQLTlsConfigTest
{
	private static final String URL = "jdbc:postgresql://10.60.40.113:5432/erp?encoding=UNICODE&options=-c%20TimeZone%3DUTC";
	private static final String CA = "/etc/metasfresh/db-ca/ca.crt";

	@Test
	void missingOptionsPreserveLocalBehavior()
	{
		assertEquals(URL, PostgreSQLTlsConfig.appendToUrl(URL, null, null));
		assertEquals(URL, PostgreSQLTlsConfig.appendToUrl(URL, " ", " "));
	}

	@Test
	void verifyFullAddsCertificateVerification()
	{
		assertEquals(URL + "&sslmode=verify-full&sslrootcert=%2Fetc%2Fmetasfresh%2Fdb-ca%2Fca.crt",
				PostgreSQLTlsConfig.appendToUrl(URL, "verify-full", CA));
	}

	@Test
	void verifyCaIsAnExplicitSupportedOption()
	{
		assertTrue(PostgreSQLTlsConfig.appendToUrl(URL, "verify-ca", CA).contains("&sslmode=verify-ca&"));
	}

	@Test
	void unsafeModesCannotDisableVerification()
	{
		for (final String mode : new String[] { "disable", "allow", "prefer", "require", "true", "verify-full&sslmode=disable" })
		{
			assertThrows(IllegalArgumentException.class, () -> PostgreSQLTlsConfig.appendToUrl(URL, mode, CA));
		}
	}

	@Test
	void verificationRequiresACertificate()
	{
		assertThrows(IllegalArgumentException.class, () -> PostgreSQLTlsConfig.appendToUrl(URL, "verify-full", null));
		assertThrows(IllegalArgumentException.class, () -> PostgreSQLTlsConfig.appendToUrl(URL, "verify-ca", ""));
	}

	@Test
	void orphanCertificateCannotSilentlyFallBackToPlaintext()
	{
		assertThrows(IllegalArgumentException.class, () -> PostgreSQLTlsConfig.appendToUrl(URL, null, CA));
		assertThrows(IllegalArgumentException.class, () -> PostgreSQLTlsConfig.appendToUrl(URL, "", CA));
	}

	@Test
	void certificatePathMustBeAbsolute()
	{
		assertThrows(IllegalArgumentException.class, () -> PostgreSQLTlsConfig.appendToUrl(URL, "verify-full", "ca.crt"));
		assertThrows(IllegalArgumentException.class, () -> PostgreSQLTlsConfig.appendToUrl(URL, "verify-full", "../ca.crt"));
	}

	@Test
	void certificatePathCannotInjectAnotherJdbcParameter() throws Exception
	{
		final String rootCert = "/etc/CMC CA &sslmode=disable?root=one.crt";
		final String result = PostgreSQLTlsConfig.appendToUrl(URL, "verify-full", rootCert);
		assertFalse(result.contains("&sslmode=disable"));
		assertEquals(4, result.substring(result.indexOf('?') + 1).split("&").length);
		final String encoded = result.substring(result.indexOf("&sslrootcert=") + "&sslrootcert=".length());
		assertEquals(rootCert, URLDecoder.decode(encoded, StandardCharsets.UTF_8.name()));
	}

	@Test
	void unicodeCertificatePathRoundTrips() throws Exception
	{
		final String rootCert = "/etc/røøt CA.crt";
		final String result = PostgreSQLTlsConfig.appendToUrl(URL, "verify-full", rootCert);
		final String encoded = result.substring(result.indexOf("&sslrootcert=") + "&sslrootcert=".length());
		assertEquals(rootCert, URLDecoder.decode(encoded, StandardCharsets.UTF_8.name()));
	}

	@Test
	void datasourceUsesTheOptInSystemProperties()
	{
		final String previousMode = System.getProperty(PostgreSQLTlsConfig.SSL_MODE_PROPERTY);
		final String previousCert = System.getProperty(PostgreSQLTlsConfig.SSL_ROOT_CERT_PROPERTY);
		try
		{
			System.setProperty(PostgreSQLTlsConfig.SSL_MODE_PROPERTY, "verify-full");
			System.setProperty(PostgreSQLTlsConfig.SSL_ROOT_CERT_PROPERTY, CA);
			assertEquals(URL + "&sslmode=verify-full&sslrootcert=%2Fetc%2Fmetasfresh%2Fdb-ca%2Fca.crt",
					new DB_PostgreSQL().getConnectionURL("10.60.40.113", 5432, "erp", "erp-runtime"));
		}
		finally
		{
			restoreProperty(PostgreSQLTlsConfig.SSL_MODE_PROPERTY, previousMode);
			restoreProperty(PostgreSQLTlsConfig.SSL_ROOT_CERT_PROPERTY, previousCert);
		}
	}

	private static void restoreProperty(final String key, final String previousValue)
	{
		if (previousValue == null)
		{
			System.clearProperty(key);
		}
		else
		{
			System.setProperty(key, previousValue);
		}
	}
}

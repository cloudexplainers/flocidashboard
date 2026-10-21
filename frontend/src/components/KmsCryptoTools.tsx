import { useState } from "react";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import FormField from "@cloudscape-design/components/form-field";
import Textarea from "@cloudscape-design/components/textarea";
import Select from "@cloudscape-design/components/select";
import Button from "@cloudscape-design/components/button";
import Alert from "@cloudscape-design/components/alert";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import Box from "@cloudscape-design/components/box";
import { executeKmsAction } from "../api/client";
import { KmsKey, canUseKmsTools } from "../pages/kmsResources";

/** Remounted for each key so plaintext and previous operation results never follow navigation. */
export function KmsCryptoTools({
  resource,
  disabled = false,
}: {
  resource: KmsKey;
  disabled?: boolean;
}) {
  const [plaintext, setPlaintext] = useState("");
  const [ciphertext, setCiphertext] = useState("");
  const [encrypted, setEncrypted] = useState("");
  const [decrypted, setDecrypted] = useState<{
    text: string;
    key: string;
  } | null>(null);
  const [dataKey, setDataKey] = useState<any>(null);
  const [revealed, setRevealed] = useState(false);
  const [spec, setSpec] = useState("AES_256");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const available = !disabled && canUseKmsTools(resource);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operation failed.");
    } finally {
      setBusy(false);
    }
  };
  const copy = (value: string) => (
    <CopyToClipboard
      variant="inline"
      textToCopy={value}
      copyButtonAriaLabel="Copy result"
      copySuccessText="Copied"
      copyErrorText="Unable to copy"
    />
  );
  return (
    <SpaceBetween size="l">
      <Alert type="info">
        These local tools use symmetric encryption keys. Decryption uses the key
        identified by the ciphertext; the returned key is shown with the result.
      </Alert>
      {!available && (
        <Alert type="warning">
          Select an enabled symmetric ENCRYPT_DECRYPT key to use these tools.
        </Alert>
      )}
      {error && <Alert type="error">{error}</Alert>}
      <Container header={<Header variant="h2">Encrypt and decrypt</Header>}>
        <SpaceBetween size="m">
          <FormField
            label="Plaintext"
            description={`Encrypt using ${resource.aliases[0] || resource.id}.`}
          >
            <Textarea
              value={plaintext}
              onChange={({ detail }) => {
                setPlaintext(detail.value);
                setEncrypted("");
              }}
              rows={3}
            />
          </FormField>
          <Button
            variant="primary"
            loading={busy}
            disabled={!available || !plaintext}
            onClick={() =>
              run(async () => {
                setEncrypted("");
                const result = await executeKmsAction("encrypt", {
                  key_id: resource.id,
                  plaintext,
                });
                if (!result.ciphertext_blob)
                  throw new Error("No ciphertext was returned.");
                setEncrypted(result.ciphertext_blob);
                setCiphertext(result.ciphertext_blob);
                setDecrypted(null);
              })
            }
          >
            Encrypt
          </Button>
          {encrypted && (
            <FormField label="Encrypted ciphertext (Base64)">
              {copy(encrypted)}
            </FormField>
          )}
          <FormField label="Ciphertext (Base64)">
            <Textarea
              value={ciphertext}
              onChange={({ detail }) => {
                setCiphertext(detail.value);
                setDecrypted(null);
              }}
              rows={3}
            />
          </FormField>
          <Button
            loading={busy}
            disabled={!available || !ciphertext.trim()}
            onClick={() =>
              run(async () => {
                setDecrypted(null);
                const result = await executeKmsAction("decrypt", {
                  ciphertext_blob: ciphertext.trim(),
                });
                if (typeof result.plaintext !== "string")
                  throw new Error("No plaintext was returned.");
                setDecrypted({
                  text: result.plaintext,
                  key: result.key_id || "Unavailable",
                });
              })
            }
          >
            Decrypt
          </Button>
          {decrypted && (
            <SpaceBetween size="s">
              <FormField label="Decrypted plaintext">
                {copy(decrypted.text)}
              </FormField>
              <FormField label="Decrypting key">
                {copy(decrypted.key)}
              </FormField>
            </SpaceBetween>
          )}
        </SpaceBetween>
      </Container>
      <Container header={<Header variant="h2">Generate data key</Header>}>
        <SpaceBetween size="m">
          <Box>
            Generate a data key for envelope encryption. The encrypted copy is
            protected by this KMS key.
          </Box>
          <FormField label="Data key spec">
            <Select
              selectedOption={{ label: spec, value: spec }}
              options={[
                { label: "AES_256", value: "AES_256" },
                { label: "AES_128", value: "AES_128" },
              ]}
              onChange={({ detail }) => {
                setSpec(detail.selectedOption.value!);
                setDataKey(null);
                setRevealed(false);
              }}
            />
          </FormField>
          <Button
            variant="primary"
            loading={busy}
            disabled={!available}
            onClick={() =>
              run(async () => {
                setDataKey(null);
                setRevealed(false);
                const result = await executeKmsAction("generate_data_key", {
                  key_id: resource.id,
                  key_spec: spec,
                });
                if (!result.plaintext_base64 || !result.ciphertext_blob)
                  throw new Error("Incomplete data key response.");
                setDataKey(result);
              })
            }
          >
            Generate data key
          </Button>
          {dataKey && (
            <SpaceBetween size="m">
              <FormField label="Plaintext data key (Base64)">
                <SpaceBetween size="s">
                  {revealed ? (
                    copy(dataKey.plaintext_base64)
                  ) : (
                    <Box>Hidden</Box>
                  )}
                  <Button onClick={() => setRevealed(!revealed)}>
                    {revealed ? "Hide plaintext key" : "Reveal plaintext key"}
                  </Button>
                </SpaceBetween>
              </FormField>
              <FormField label="Encrypted data key (Base64)">
                {copy(dataKey.ciphertext_blob)}
              </FormField>
              <FormField label="KMS key">
                {copy(dataKey.key_id || resource.id)}
              </FormField>
              <Button
                onClick={() => {
                  setDataKey(null);
                  setRevealed(false);
                }}
              >
                Clear data key output
              </Button>
            </SpaceBetween>
          )}
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
}

import React, { useEffect, useMemo, useState } from "react";
import Table from "../components/ResourceTable";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Container from "@cloudscape-design/components/container";
import Modal from "@cloudscape-design/components/modal";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Select from "@cloudscape-design/components/select";
import Badge from "@cloudscape-design/components/badge";
import Alert from "@cloudscape-design/components/alert";
import TextFilter from "@cloudscape-design/components/text-filter";
import Tabs from "@cloudscape-design/components/tabs";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import { CodeSnippet } from "../components/CodeSnippet";
import { useSession } from "../components/SessionContext";
import {
  EcrImage,
  ecrRepositories,
  ecrDate,
  ecrSize,
  shellQuote,
} from "./ecrResources";
import {
  createEcrRepository,
  deleteEcrRepository,
  batchDeleteEcrImages,
  putEcrTagMutability,
  putEcrLifecyclePolicy,
  runEcrGarbageCollection,
} from "../api/client";

interface ECRConsoleProps {
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
  resourcePath?: string;
  onNavigate?: (path: string) => void;
}
const unavailable = "Unavailable";
const copy = (value?: string) =>
  value ? (
    <CopyToClipboard
      copyButtonAriaLabel={`Copy ${value}`}
      copySuccessText="Copied"
      copyErrorText="Unable to copy"
      textToCopy={value}
      variant="inline"
    />
  ) : (
    unavailable
  );
const imageKey = (image: EcrImage) => image.digest || image.tags[0] || "";

export const ECRConsole: React.FC<ECRConsoleProps> = ({
  activeTab,
  onTabChange,
  resourcePath,
  onNavigate,
}) => {
  const { identity } = useSession();
  const [data, setData] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [filter, setFilter] = useState("");
  const [imageFilter, setImageFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("all");
  const [message, setMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);
  const [selectedImages, setSelectedImages] = useState<EcrImage[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [repoName, setRepoName] = useState("");
  const [mutability, setMutability] = useState("MUTABLE");
  const [pushOpen, setPushOpen] = useState(false);
  const [imageTag, setImageTag] = useState("latest");
  const [lifecycleDoc, setLifecycleDoc] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<
    "repository" | "images" | null
  >(null);
  const route = new URLSearchParams(
    (resourcePath || window.location.search).split("?").pop(),
  );
  const repoQuery = route.get("repository") || "";
  const imageQuery = route.get("image") || "";
  const tab = (activeTab || "repositories").split("?")[0];
  const repositories = useMemo(() => ecrRepositories(data), [data]);
  const repository = repositories.find((repo) => repo.name === repoQuery);
  const image = repository?.images.find(
    (item) => imageKey(item) === imageQuery,
  );

  const navigate = (nextTab: string, name = "", digest = "") => {
    const query = new URLSearchParams();
    if (name) query.set("repository", name);
    if (digest) query.set("image", digest);
    const target = `${nextTab}${query.size ? `?${query}` : ""}`;
    if (onNavigate) onNavigate(`ecr/${target}`);
    else onTabChange?.(target);
  };
  const loadData = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const response = await fetch("/api/ecr/");
      const result = await response.json();
      if (!response.ok || result.error)
        throw new Error(result.error || `HTTP ${response.status}`);
      setData(result);
      setSelectedImages([]);
    } catch (error) {
      setLoadError(
        error instanceof Error
          ? error.message
          : "Unable to load ECR resources.",
      );
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void loadData();
  }, [
    identity?.account_id,
    identity?.region,
    identity?.arn,
    identity?.endpoint,
  ]);
  useEffect(() => {
    setSelectedImages([]);
    setImageFilter("");
    setTagFilter("all");
    setPushOpen(false);
    setConfirmation(null);
    setMessage(null);
  }, [repoQuery]);
  useEffect(() => {
    setLifecycleDoc(repository?.lifecycle || "");
  }, [repository?.name, repository?.lifecycle]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await action();
    } catch (error) {
      setMessage({
        type: "error",
        text: error instanceof Error ? error.message : "Operation failed.",
      });
    } finally {
      setBusy(false);
    }
  };
  const createRepository = () =>
    run(async () => {
      await createEcrRepository(
        repoName.trim(),
        mutability as "MUTABLE" | "IMMUTABLE",
      );
      setCreateOpen(false);
      await loadData();
      navigate("images", repoName.trim());
      setRepoName("");
    });
  const deleteResources = () =>
    run(async () => {
      if (!repository) return;
      if (confirmation === "repository") {
        await deleteEcrRepository(repository.name, true);
        setConfirmation(null);
        navigate("repositories");
        await loadData();
        setMessage({
          type: "success",
          text: `Repository "${repository.name}" deleted.`,
        });
      } else {
        const result = await batchDeleteEcrImages(
          repository.name,
          selectedImages.map((item) =>
            item.digest
              ? { imageDigest: item.digest }
              : { imageTag: item.tags[0] },
          ),
        );
        setConfirmation(null);
        setSelectedImages([]);
        await loadData();
        const failures = result.failures || [];
        setMessage({
          type: failures.length ? "error" : "success",
          text: failures.length
            ? `Some images could not be deleted: ${failures.map((f: any) => f.failureReason || f.failureCode).join("; ")}`
            : "Selected images deleted.",
        });
      }
    });
  const visibleImages = (repository?.images || []).filter(
    (item) =>
      `${item.digest || ""} ${item.tags.join(" ")}`
        .toLowerCase()
        .includes(imageFilter.toLowerCase()) &&
      (tagFilter === "all" ||
        (tagFilter === "tagged"
          ? item.tags.length > 0
          : item.tags.length === 0)),
  );
  const validRepoName =
    /^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*\/)*[a-z0-9]+(?:[._-][a-z0-9]+)*$/.test(
      repoName,
    ) &&
    repoName.length >= 2 &&
    repoName.length <= 256;
  const validTag = /^[\w][\w.-]{0,127}$/.test(imageTag);
  const repoUri = repository?.uri?.replace(/^https?:\/\//, "");
  const registry = repoUri?.split("/")[0];
  const region = repository?.arn?.split(":")[3] || identity?.region;
  const canAuthenticate = !!(
    registry &&
    region &&
    region !== "Unknown" &&
    identity?.endpoint &&
    identity.endpoint !== "Unavailable"
  );
  const loginCommand = canAuthenticate
    ? `aws ecr get-login-password --region ${shellQuote(region!)} --endpoint-url ${shellQuote(identity!.endpoint)} | docker login --username AWS --password-stdin ${shellQuote(registry!)}`
    : "";
  const imageUri =
    repoUri && image
      ? image.digest
        ? `${repoUri}@${image.digest}`
        : image.tags[0]
          ? `${repoUri}:${image.tags[0]}`
          : undefined
      : undefined;
  const tagBadges = (item: EcrImage) =>
    item.tags.length ? (
      <SpaceBetween direction="horizontal" size="xxs">
        {item.tags.map((tag) => (
          <Badge key={tag} color="blue">
            {tag}
          </Badge>
        ))}
      </SpaceBetween>
    ) : (
      <Badge color="grey">Untagged</Badge>
    );

  return (
    <SpaceBetween size="l">
      <Header
        variant="h1"
        description={
          repository
            ? "Manage container images and repository settings."
            : "Store, manage, and deploy container images in your private registry."
        }
        actions={
          <SpaceBetween direction="horizontal" size="xs">
            <Button iconName="refresh" loading={loading} onClick={loadData}>
              Refresh
            </Button>
            {repository ? (
              <>
                <Button
                  disabled={busy}
                  onClick={() => setConfirmation("repository")}
                >
                  Delete repository
                </Button>
                <Button
                  variant="primary"
                  disabled={!repoUri}
                  onClick={() => setPushOpen(true)}
                >
                  View push commands
                </Button>
              </>
            ) : (
              <Button variant="primary" onClick={() => setCreateOpen(true)}>
                Create repository
              </Button>
            )}
          </SpaceBetween>
        }
      >
        {imageQuery
          ? "Image details"
          : repository?.name || "Private repositories"}
      </Header>
      {loadError && (
        <Alert type="error" header="Unable to load ECR resources">
          {loadError} Refresh to retry. Previously loaded resources may be out
          of date.
        </Alert>
      )}
      {repository?.issues.length ? (
        <Alert type="warning" header="Some repository details are unavailable">
          {repository.issues.join(" ")} Refresh to retry.
        </Alert>
      ) : null}
      {message && (
        <Alert
          type={message.type}
          dismissible
          onDismiss={() => setMessage(null)}
        >
          {message.text}
        </Alert>
      )}
      {repoQuery && !repository ? (
        <Container>
          <Box textAlign="center" padding="l">
            <SpaceBetween size="m">
              <Box>
                {loading
                  ? "Loading repository…"
                  : `Repository "${repoQuery}" was not found in the current account and Region.`}
              </Box>
              <Button onClick={() => navigate("repositories")}>
                View repositories
              </Button>
            </SpaceBetween>
          </Box>
        </Container>
      ) : !repository ? (
        <>
          <Container header={<Header variant="h2">Registry overview</Header>}>
            <KeyValuePairs
              columns={3}
              items={[
                {
                  label: "Repositories",
                  value: loading ? "Loading…" : repositories.length,
                },
                {
                  label: "Images",
                  value: loading
                    ? "Loading…"
                    : repositories.reduce(
                        (count, repo) => count + repo.images.length,
                        0,
                      ),
                },
                { label: "Region", value: identity?.region || unavailable },
              ]}
            />
          </Container>
          <Table
            loading={loading}
            loadingText="Loading repositories"
            trackBy="name"
            header={
              <Header variant="h2" counter={`(${repositories.length})`}>
                Repositories
              </Header>
            }
            filter={
              <TextFilter
                filteringText={filter}
                filteringAriaLabel="Find repositories"
                filteringPlaceholder="Find repositories by name"
                onChange={({ detail }) => setFilter(detail.filteringText)}
              />
            }
            items={repositories.filter((repo) =>
              repo.name.toLowerCase().includes(filter.toLowerCase()),
            )}
            columnDefinitions={[
              {
                id: "name",
                header: "Repository name",
                sortingField: "name",
                cell: (item) => (
                  <Button
                    variant="inline-link"
                    onClick={() => navigate("images", item.name)}
                  >
                    {item.name}
                  </Button>
                ),
              },
              { id: "uri", header: "URI", cell: (item) => copy(item.uri) },
              {
                id: "images",
                header: "Images",
                sortingComparator: (a, b) => a.images.length - b.images.length,
                cell: (item) => item.images.length,
              },
              {
                id: "mutability",
                header: "Tag mutability",
                cell: (item) => item.mutability || unavailable,
              },
              {
                id: "created",
                header: "Created at",
                cell: (item) => ecrDate(item.created),
              },
            ]}
            empty={
              <Box textAlign="center" padding="l">
                <SpaceBetween size="m">
                  <Box variant="strong">
                    {filter ? "No matching repositories" : "No repositories"}
                  </Box>
                  <Box>
                    {filter
                      ? "Try a different name."
                      : "Create a repository to start storing container images."}
                  </Box>
                  <Button
                    onClick={() =>
                      filter ? setFilter("") : setCreateOpen(true)
                    }
                  >
                    {filter ? "Clear filter" : "Create repository"}
                  </Button>
                </SpaceBetween>
              </Box>
            }
          />
          <ExpandableSection headerText="Local registry maintenance">
            <SpaceBetween size="m">
              <Box>
                Garbage collection is a Floci maintenance operation for the
                local registry.
              </Box>
              <Button
                loading={busy}
                onClick={() =>
                  run(async () => {
                    await runEcrGarbageCollection();
                    await loadData();
                    setMessage({
                      type: "success",
                      text: "Local registry garbage collection completed.",
                    });
                  })
                }
              >
                Run garbage collection
              </Button>
            </SpaceBetween>
          </ExpandableSection>
        </>
      ) : (
        <>
          <Container header={<Header variant="h2">Repository overview</Header>}>
            <KeyValuePairs
              columns={3}
              items={[
                { label: "URI", value: copy(repository.uri) },
                { label: "ARN", value: copy(repository.arn) },
                {
                  label: "Registry ID",
                  value: repository.registryId || unavailable,
                },
                { label: "Created at", value: ecrDate(repository.created) },
                {
                  label: "Tag mutability",
                  value: repository.mutability || unavailable,
                },
                {
                  label: "Encryption",
                  value: repository.encryption || unavailable,
                },
              ]}
            />
          </Container>
          {imageQuery ? (
            image ? (
              <SpaceBetween size="l">
                <Container
                  header={
                    <Header
                      variant="h2"
                      actions={
                        <Button
                          onClick={() => navigate("images", repository.name)}
                        >
                          View all images
                        </Button>
                      }
                    >
                      Image overview
                    </Header>
                  }
                >
                  <KeyValuePairs
                    columns={2}
                    items={[
                      { label: "Image tags", value: tagBadges(image) },
                      { label: "Digest", value: copy(image.digest) },
                      { label: "Image URI", value: copy(imageUri) },
                      { label: "Size", value: ecrSize(image.size) },
                      { label: "Pushed at", value: ecrDate(image.pushed) },
                      {
                        label: "Manifest media type",
                        value: image.mediaType || unavailable,
                      },
                      {
                        label: "Scan status",
                        value: image.scanStatus || unavailable,
                      },
                    ]}
                  />
                </Container>
                {imageUri && (
                  <Container header={<Header variant="h2">Pull image</Header>}>
                    <SpaceBetween size="m">
                      <Box>
                        Authenticate with the registry before pulling a private
                        image.
                      </Box>
                      {loginCommand && (
                        <CodeSnippet language="cli" code={loginCommand} />
                      )}
                      <CodeSnippet
                        language="cli"
                        code={`docker pull ${shellQuote(imageUri)}`}
                      />
                    </SpaceBetween>
                  </Container>
                )}
              </SpaceBetween>
            ) : (
              <Alert type="warning" header="Image not found">
                This image may have been deleted.{" "}
                <Button
                  variant="inline-link"
                  onClick={() => navigate("images", repository.name)}
                >
                  View all images
                </Button>
              </Alert>
            )
          ) : (
            <Tabs
              activeTabId={tab === "repositories" ? "images" : tab}
              onChange={({ detail }) =>
                navigate(detail.activeTabId, repository.name)
              }
              tabs={[
                {
                  id: "images",
                  label: "Images",
                  content: (
                    <Table
                      loading={loading}
                      loadingText="Loading images"
                      trackBy={imageKey}
                      header={
                        <Header
                          variant="h2"
                          counter={`(${repository.images.length})`}
                          description="Select an image digest to view its details and pull command."
                          actions={
                            <Button
                              disabled={!selectedImages.length || busy}
                              onClick={() => setConfirmation("images")}
                            >
                              Delete images
                            </Button>
                          }
                        >
                          Images
                        </Header>
                      }
                      filter={
                        <SpaceBetween direction="horizontal" size="s">
                          <TextFilter
                            filteringText={imageFilter}
                            filteringAriaLabel="Find images"
                            filteringPlaceholder="Find images by tag or digest"
                            onChange={({ detail }) => {
                              setImageFilter(detail.filteringText);
                              setSelectedImages([]);
                            }}
                          />
                          <Select
                            selectedOption={{
                              label:
                                tagFilter === "all"
                                  ? "All images"
                                  : tagFilter === "tagged"
                                    ? "Tagged images"
                                    : "Untagged images",
                              value: tagFilter,
                            }}
                            ariaLabel="Image tag status"
                            options={[
                              { label: "All images", value: "all" },
                              { label: "Tagged images", value: "tagged" },
                              { label: "Untagged images", value: "untagged" },
                            ]}
                            onChange={({ detail }) => {
                              setTagFilter(detail.selectedOption.value!);
                              setSelectedImages([]);
                            }}
                          />
                        </SpaceBetween>
                      }
                      items={visibleImages}
                      selectionType="multi"
                      selectedItems={selectedImages}
                      onSelectionChange={({ detail }) =>
                        setSelectedImages(detail.selectedItems)
                      }
                      columnDefinitions={[
                        { id: "tags", header: "Image tags", cell: tagBadges },
                        {
                          id: "digest",
                          header: "Image digest",
                          sortingField: "digest",
                          cell: (item) =>
                            imageKey(item) ? (
                              <Button
                                variant="inline-link"
                                onClick={() =>
                                  navigate(
                                    "images",
                                    repository.name,
                                    imageKey(item),
                                  )
                                }
                              >
                                {item.digest || item.tags[0]}
                              </Button>
                            ) : (
                              unavailable
                            ),
                        },
                        {
                          id: "size",
                          header: "Size",
                          sortingField: "size",
                          cell: (item) => ecrSize(item.size),
                        },
                        {
                          id: "pushed",
                          header: "Pushed at",
                          cell: (item) => ecrDate(item.pushed),
                        },
                        {
                          id: "scanStatus",
                          header: "Scan status",
                          cell: (item) => item.scanStatus || unavailable,
                        },
                      ]}
                      empty={
                        <Box textAlign="center" padding="l">
                          <SpaceBetween size="m">
                            <Box variant="strong">
                              {imageFilter || tagFilter !== "all"
                                ? "No matching images"
                                : "No images"}
                            </Box>
                            <Box>
                              Push a container image to this repository or
                              adjust the image filters.
                            </Box>
                            <Button
                              disabled={!repoUri}
                              onClick={() => setPushOpen(true)}
                            >
                              View push commands
                            </Button>
                          </SpaceBetween>
                        </Box>
                      }
                    />
                  ),
                },
                {
                  id: "lifecycle",
                  label: "Lifecycle policy & settings",
                  content: (
                    <SpaceBetween size="l">
                      <Container
                        header={<Header variant="h2">Lifecycle policy</Header>}
                      >
                        <SpaceBetween size="m">
                          {!repository.lifecycle && (
                            <Alert type="info">
                              No lifecycle policy is configured for this
                              repository.
                            </Alert>
                          )}
                          <FormField
                            label="Lifecycle policy JSON"
                            description="Define rules to expire images automatically."
                          >
                            <Textarea
                              rows={12}
                              value={lifecycleDoc}
                              onChange={({ detail }) =>
                                setLifecycleDoc(detail.value)
                              }
                              placeholder={'{ "rules": [] }'}
                            />
                          </FormField>
                          <Button
                            variant="primary"
                            loading={busy}
                            disabled={!lifecycleDoc.trim() || loading}
                            onClick={() =>
                              run(async () => {
                                try {
                                  JSON.parse(lifecycleDoc);
                                } catch {
                                  throw new Error(
                                    "Enter valid JSON for the lifecycle policy.",
                                  );
                                }
                                await putEcrLifecyclePolicy(
                                  repository.name,
                                  lifecycleDoc,
                                );
                                await loadData();
                                setMessage({
                                  type: "success",
                                  text: "Lifecycle policy saved.",
                                });
                              })
                            }
                          >
                            Save policy
                          </Button>
                        </SpaceBetween>
                      </Container>
                      <Container
                        header={
                          <Header variant="h2">Repository settings</Header>
                        }
                      >
                        <SpaceBetween size="m">
                          <KeyValuePairs
                            columns={2}
                            items={[
                              {
                                label: "Tag mutability",
                                value: repository.mutability || unavailable,
                              },
                              {
                                label: "Scan on push",
                                value:
                                  repository.scanOnPush == null
                                    ? unavailable
                                    : repository.scanOnPush
                                      ? "Enabled"
                                      : "Disabled",
                              },
                            ]}
                          />
                          <Button
                            loading={busy}
                            disabled={!repository.mutability || loading}
                            onClick={() =>
                              run(async () => {
                                await putEcrTagMutability(
                                  repository.name,
                                  repository.mutability === "IMMUTABLE"
                                    ? "MUTABLE"
                                    : "IMMUTABLE",
                                );
                                await loadData();
                                setMessage({
                                  type: "success",
                                  text: "Tag mutability updated.",
                                });
                              })
                            }
                          >
                            Switch to{" "}
                            {repository.mutability === "IMMUTABLE"
                              ? "mutable"
                              : "immutable"}{" "}
                            tags
                          </Button>
                        </SpaceBetween>
                      </Container>
                    </SpaceBetween>
                  ),
                },
                {
                  id: "permissions",
                  label: "Permissions",
                  content: (
                    <Container
                      header={<Header variant="h2">Repository policy</Header>}
                    >
                      {repository.policy ? (
                        <CodeSnippet language="json" code={repository.policy} />
                      ) : (
                        <Box>No repository policy is configured.</Box>
                      )}
                    </Container>
                  ),
                },
                {
                  id: "tags",
                  label: "Tags",
                  content: (
                    <Table
                      header={
                        <Header
                          variant="h2"
                          counter={`(${repository.tags.length})`}
                        >
                          Repository tags
                        </Header>
                      }
                      items={repository.tags}
                      trackBy="Key"
                      columnDefinitions={[
                        { id: "Key", header: "Key", cell: (item) => item.Key },
                        {
                          id: "Value",
                          header: "Value",
                          cell: (item) => item.Value,
                        },
                      ]}
                      empty={
                        <Box textAlign="center" padding="l">
                          No tags associated with this repository.
                        </Box>
                      }
                    />
                  ),
                },
              ]}
            />
          )}
        </>
      )}
      <Modal
        visible={createOpen}
        onDismiss={() => !busy && setCreateOpen(false)}
        header="Create private repository"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={busy}
                onClick={() => setCreateOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                disabled={!validRepoName}
                onClick={createRepository}
              >
                Create repository
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="l">
          <FormField
            label="Repository name"
            description="Use 2–256 lowercase letters, numbers, and separators. A namespace such as my-service/backend is supported."
            errorText={
              repoName && !validRepoName
                ? "Enter a valid repository name using lowercase letters, numbers, and . _ - / separators."
                : undefined
            }
          >
            <Input
              value={repoName}
              onChange={({ detail }) => setRepoName(detail.value)}
              placeholder="my-service/backend"
            />
          </FormField>
          <FormField
            label="Image tag mutability"
            description="Immutable tags prevent an existing image tag from being overwritten."
          >
            <Select
              selectedOption={{
                label: mutability === "MUTABLE" ? "Mutable" : "Immutable",
                value: mutability,
              }}
              options={[
                { label: "Mutable", value: "MUTABLE" },
                { label: "Immutable", value: "IMMUTABLE" },
              ]}
              onChange={({ detail }) =>
                setMutability(detail.selectedOption.value!)
              }
            />
          </FormField>
        </SpaceBetween>
      </Modal>
      <Modal
        visible={pushOpen && !!repository}
        onDismiss={() => setPushOpen(false)}
        size="large"
        header={`Push commands for ${repository?.name}`}
        footer={
          <Box float="right">
            <Button variant="primary" onClick={() => setPushOpen(false)}>
              Done
            </Button>
          </Box>
        }
      >
        <SpaceBetween size="l">
          <Alert type="info">
            Run these commands in a local terminal with Docker running. The
            login command uses your configured Floci endpoint and AWS CLI
            credentials.
          </Alert>
          <FormField
            label="Image tag"
            errorText={
              !validTag
                ? "Use up to 128 letters, numbers, underscores, periods, or hyphens; start with a letter, number, or underscore."
                : undefined
            }
          >
            <Input
              value={imageTag}
              onChange={({ detail }) => setImageTag(detail.value)}
            />
          </FormField>
          {loginCommand ? (
            <FormField label="1. Authenticate Docker">
              <CodeSnippet language="cli" code={loginCommand} />
            </FormField>
          ) : (
            <Alert type="warning">
              Registry, Region, or endpoint metadata is unavailable. Check the
              session and refresh before generating an authentication command.
            </Alert>
          )}
          {validTag && repository && repoUri && (
            <>
              <FormField label="2. Build the image">
                <CodeSnippet
                  language="cli"
                  code={`docker build -t ${shellQuote(`${repository.name}:${imageTag}`)} .`}
                />
              </FormField>
              <FormField label="3. Tag the image">
                <CodeSnippet
                  language="cli"
                  code={`docker tag ${shellQuote(`${repository.name}:${imageTag}`)} ${shellQuote(`${repoUri}:${imageTag}`)}`}
                />
              </FormField>
              <FormField label="4. Push the image">
                <CodeSnippet
                  language="cli"
                  code={`docker push ${shellQuote(`${repoUri}:${imageTag}`)}`}
                />
              </FormField>
            </>
          )}
        </SpaceBetween>
      </Modal>
      <Modal
        visible={!!confirmation}
        onDismiss={() => !busy && setConfirmation(null)}
        header={
          confirmation === "repository" ? "Delete repository" : "Delete images"
        }
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                disabled={busy}
                onClick={() => setConfirmation(null)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                loading={busy}
                onClick={deleteResources}
              >
                Delete
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Alert type="warning">
          {confirmation === "repository"
            ? `Permanently delete repository "${repository?.name}" and all of its images?`
            : `Permanently delete ${selectedImages.length} selected images from "${repository?.name}"? Deleting an image by digest removes all of its tags.`}
        </Alert>
      </Modal>
    </SpaceBetween>
  );
};

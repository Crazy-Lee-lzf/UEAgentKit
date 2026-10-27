// P0 native evidence for the read-only PIE runtime inspection surface.
//
// These tests exercise the exact same bounded functions the Editor Bridge
// handlers call (PieRuntimeInspection.h), so they cover the real Editor memory
// path without opening a socket.  Nothing here writes an asset, a package, or a
// production index; the Actor used for inspection is spawned inside a
// disposable PIE world only.

#include "PieRuntimeInspection.h"

#include "Components/StaticMeshComponent.h"
#include "Editor.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Misc/AutomationTest.h"
#include "Misc/DateTime.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Settings/LevelEditorPlaySettings.h"
#include "Tests/AutomationEditorCommon.h"
#include "UObject/UObjectGlobals.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitP0PieRuntime
{
	const TCHAR* const EvidenceRelativeDirectory = TEXT("P0Pie");

	bool WriteJson(const FString& Path, const TSharedRef<FJsonObject>& Object)
	{
		FString Text;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Text);
		if (!FJsonSerializer::Serialize(Object, Writer))
		{
			return false;
		}
		IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
		return FFileHelper::SaveStringToFile(
			Text,
			*Path,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
	}

	const TSharedPtr<FJsonObject>* FindObjectField(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field)
	{
		if (!Object.IsValid())
		{
			return nullptr;
		}
		const TSharedPtr<FJsonObject>* Value = nullptr;
		Object->TryGetObjectField(Field, Value);
		return Value != nullptr && Value->IsValid() ? Value : nullptr;
	}

	const TArray<TSharedPtr<FJsonValue>>* FindArrayField(const TSharedPtr<FJsonObject>& Object, const TCHAR* Field)
	{
		if (!Object.IsValid())
		{
			return nullptr;
		}
		const TArray<TSharedPtr<FJsonValue>>* Value = nullptr;
		Object->TryGetArrayField(Field, Value);
		return Value;
	}

	bool FindObjectItemWithString(
		const TSharedPtr<FJsonObject>& Object,
		const TCHAR* ArrayField,
		const TCHAR* StringField,
		const FString& Expected,
		TSharedPtr<FJsonObject>& OutItem)
	{
		const TArray<TSharedPtr<FJsonValue>>* Items = FindArrayField(Object, ArrayField);
		if (Items == nullptr)
		{
			return false;
		}
		for (const TSharedPtr<FJsonValue>& Value : *Items)
		{
			TSharedPtr<FJsonObject> Item;
			if (Value.IsValid())
			{
				Item = Value->AsObject();
			}
			FString Text;
			if (Item.IsValid() && Item->TryGetStringField(StringField, Text) && Text == Expected)
			{
				OutItem = Item;
				return true;
			}
		}
		return false;
	}

	bool ArrayContainsString(const TArray<TSharedPtr<FJsonValue>>* Items, const FString& Expected)
	{
		if (Items == nullptr)
		{
			return false;
		}
		for (const TSharedPtr<FJsonValue>& Value : *Items)
		{
			FString Text;
			if (Value.IsValid() && Value->TryGetString(Text) && Text == Expected)
			{
				return true;
			}
		}
		return false;
	}

	struct FRuntimeState
	{
		FAutomationTestBase* Test = nullptr;

		TEnumAsByte<EPlayNetMode> OriginalPlayNetMode = PIE_Standalone;
		int32 OriginalPlayNumberOfClients = 1;
		bool bOriginalRunUnderOneProcess = false;
		bool bOriginalLaunchSeparateServer = false;
		bool bSettingsCaptured = false;

		int32 ServerInstanceId = INDEX_NONE;
		int32 ClientInstanceId = INDEX_NONE;
		FString ServerWorldPath;
		FString ServerContextId;
		FString ClientWorldPath;
		int32 ContextCount = 0;
		int32 ServerCount = 0;
		int32 ClientCount = 0;

		FString SpawnedActorPath;
		FString SpawnedActorGuid;
		FString SpawnedActorName;
		FString SpawnedActorLabel;
		FString CubeAssetPath;

		bool bLabelAmbiguityVerified = false;
		bool bClientWorldVerified = false;
		bool bTransformVerified = false;
		bool bComponentAssetVerified = false;
		bool bBoundsVerified = false;
		bool bContextListingRecorded = false;

		TSharedPtr<FJsonObject> ContextListing;
		TSharedPtr<FJsonObject> ActorInspection;
		TSharedPtr<FJsonObject> CodeEvidence;
		TSharedPtr<FJsonObject> BoundsEvidence;

		double StartedAt = 0.0;
		double ReadySince = 0.0;
		double StopPhaseStartedAt = 0.0;
		bool bChecksCompleted = false;
		bool bStopPhaseCompleted = false;
	};

	void RestorePlaySettings(const TSharedRef<FRuntimeState>& State)
	{
		if (!State->bSettingsCaptured)
		{
			return;
		}
		ULevelEditorPlaySettings* PlaySettings = GetMutableDefault<ULevelEditorPlaySettings>();
		if (PlaySettings == nullptr)
		{
			return;
		}
		PlaySettings->SetPlayNetMode(State->OriginalPlayNetMode);
		PlaySettings->SetPlayNumberOfClients(State->OriginalPlayNumberOfClients);
		PlaySettings->SetRunUnderOneProcess(State->bOriginalRunUnderOneProcess);
		PlaySettings->bLaunchSeparateServer = State->bOriginalLaunchSeparateServer;
		State->bSettingsCaptured = false;
	}

	/** Write the observed native evidence for one phase of the run. */
	void WriteRuntimeEvidence(
		const TSharedRef<FRuntimeState>& State,
		const TCHAR* FileName,
		const TSharedRef<FJsonObject>& ExtraFields)
	{
		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p0pie.runtime-inspection/1.0"));
		Evidence->SetNumberField(TEXT("processId"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
		Evidence->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
		Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
		Evidence->SetStringField(TEXT("source"), TEXT("native-automation-real-pie"));
		Evidence->SetNumberField(TEXT("pieContextCount"), State->ContextCount);
		Evidence->SetNumberField(TEXT("serverContextCount"), State->ServerCount);
		Evidence->SetNumberField(TEXT("clientContextCount"), State->ClientCount);
		Evidence->SetNumberField(TEXT("serverPieInstanceId"), State->ServerInstanceId);
		Evidence->SetNumberField(TEXT("clientPieInstanceId"), State->ClientInstanceId);
		Evidence->SetStringField(TEXT("serverWorldPath"), State->ServerWorldPath);
		Evidence->SetStringField(TEXT("clientWorldPath"), State->ClientWorldPath);
		Evidence->SetStringField(TEXT("spawnedActorPath"), State->SpawnedActorPath);
		Evidence->SetStringField(TEXT("spawnedActorGuid"), State->SpawnedActorGuid);
		Evidence->SetStringField(TEXT("componentAssetPath"), State->CubeAssetPath);
		Evidence->SetBoolField(TEXT("labelAmbiguityVerified"), State->bLabelAmbiguityVerified);
		Evidence->SetBoolField(TEXT("clientWorldVerified"), State->bClientWorldVerified);
		Evidence->SetBoolField(TEXT("transformVerified"), State->bTransformVerified);
		Evidence->SetBoolField(TEXT("componentAssetVerified"), State->bComponentAssetVerified);
		Evidence->SetBoolField(TEXT("boundsVerified"), State->bBoundsVerified);
		if (State->ContextListing.IsValid())
		{
			Evidence->SetObjectField(TEXT("worldContexts"), State->ContextListing.ToSharedRef());
		}
		if (State->ActorInspection.IsValid())
		{
			Evidence->SetObjectField(TEXT("actorInspection"), State->ActorInspection.ToSharedRef());
		}
		if (State->CodeEvidence.IsValid())
		{
			Evidence->SetObjectField(TEXT("codes"), State->CodeEvidence.ToSharedRef());
		}
		if (State->BoundsEvidence.IsValid())
		{
			Evidence->SetObjectField(TEXT("bounds"), State->BoundsEvidence.ToSharedRef());
		}
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Field : ExtraFields->Values)
		{
			Evidence->SetField(Field.Key, Field.Value);
		}

		const FString Path = FPaths::Combine(
			FPaths::ProjectSavedDir(),
			EvidenceRelativeDirectory,
			FileName);
		const bool bWritten = WriteJson(Path, Evidence);
		UE_LOG(
			LogTemp,
			Display,
			TEXT("P0Pie native evidence path=%s written=%s"),
			*Path,
			bWritten ? TEXT("true") : TEXT("false"));
		State->Test->TestTrue(
			*FString::Printf(TEXT("P0Pie native evidence is written: %s"), FileName),
			bWritten);
	}

	class FWaitForPieContexts final : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForPieContexts(TSharedRef<FRuntimeState> InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			if (State->bChecksCompleted)
			{
				return true;
			}
			if (State->StartedAt <= 0.0)
			{
				State->StartedAt = FPlatformTime::Seconds();
			}
			const double Now = FPlatformTime::Seconds();
			TArray<UEAgentKitPieRuntime::FWorldContextSnapshot> Contexts;
			bool bTruncated = false;
			UEAgentKitPieRuntime::CollectPieWorldContexts(Contexts, bTruncated);
			if (Contexts.Num() >= 2 && !bTruncated)
			{
				if (State->ReadySince <= 0.0)
				{
					State->ReadySince = Now;
				}
				else if (Now - State->ReadySince >= 1.0)
				{
					RunChecks();
					State->bChecksCompleted = true;
					return true;
				}
			}
			if (Now - State->StartedAt > 180.0)
			{
				State->Test->AddError(
					TEXT("PIE did not produce at least two live PIE world contexts (listen server + client) within 180 seconds."));
				State->bChecksCompleted = true;
				return true;
			}
			return false;
		}

	private:
		void RunChecks()
		{
			using namespace UEAgentKitPieRuntime;
			FAutomationTestBase* Test = State->Test;

			// ---- bounded listing -------------------------------------------------
			TSharedRef<FJsonObject> Listing = BuildWorldContextsJson(16);
			State->ContextListing = Listing;
			State->bContextListingRecorded = true;
			Test->TestTrue(TEXT("P0Pie listing reports PIE running"), Listing->GetBoolField(TEXT("running")));
			Test->TestTrue(TEXT("P0Pie listing is read-only"), Listing->GetBoolField(TEXT("readOnly")));
			Test->TestFalse(TEXT("P0Pie listing does not need the database"), Listing->GetBoolField(TEXT("requiresDatabase")));
			Test->TestTrue(
				TEXT("P0Pie listing reports a unique PIE instance identity set"),
				Listing->GetBoolField(TEXT("identitiesUnique")));
			Test->TestFalse(TEXT("P0Pie listing is not truncated at 16 contexts"), Listing->GetBoolField(TEXT("truncated")));
			State->ContextCount = static_cast<int32>(Listing->GetNumberField(TEXT("contextCount")));
			State->ServerCount = static_cast<int32>(Listing->GetNumberField(TEXT("serverCount")));
			State->ClientCount = static_cast<int32>(Listing->GetNumberField(TEXT("clientCount")));
			Test->TestTrue(TEXT("P0Pie listing reports at least two PIE world contexts"), State->ContextCount >= 2);
			Test->TestTrue(TEXT("P0Pie listing reports a server world"), State->ServerCount >= 1);
			Test->TestTrue(TEXT("P0Pie listing reports a client world"), State->ClientCount >= 1);

			TSharedPtr<FJsonObject> ServerItem;
			TSharedPtr<FJsonObject> ClientItem;
			if (FindObjectItemWithString(Listing, TEXT("items"), TEXT("serverOrClient"), TEXT("server"), ServerItem))
			{
				State->ServerInstanceId = static_cast<int32>(ServerItem->GetNumberField(TEXT("pieInstanceId")));
				State->ServerWorldPath = ServerItem->GetStringField(TEXT("worldPath"));
				State->ServerContextId = ServerItem->GetStringField(TEXT("contextId"));
				Test->TestTrue(TEXT("P0Pie server context has a valid PIE instance id"), State->ServerInstanceId >= 0);
				Test->TestTrue(TEXT("P0Pie server context is flagged as server"), ServerItem->GetBoolField(TEXT("isServer")));
				Test->TestFalse(TEXT("P0Pie server context is not flagged as client"), ServerItem->GetBoolField(TEXT("isClient")));
				Test->TestTrue(
					TEXT("P0Pie server context is a listen or dedicated server net mode"),
					ServerItem->GetBoolField(TEXT("isListenServer")) || ServerItem->GetBoolField(TEXT("isDedicatedServer")));
				Test->TestTrue(
					TEXT("P0Pie server context id embeds the instance id and net mode"),
					State->ServerContextId.Contains(FString::Printf(TEXT("%d"), State->ServerInstanceId)));
			}
			else
			{
				Test->AddError(TEXT("P0Pie listing has no server world context item."));
			}

			if (FindObjectItemWithString(Listing, TEXT("items"), TEXT("serverOrClient"), TEXT("client"), ClientItem))
			{
				State->ClientInstanceId = static_cast<int32>(ClientItem->GetNumberField(TEXT("pieInstanceId")));
				State->ClientWorldPath = ClientItem->GetStringField(TEXT("worldPath"));
				Test->TestTrue(TEXT("P0Pie client context has a valid PIE instance id"), State->ClientInstanceId >= 0);
				Test->TestTrue(TEXT("P0Pie client context is flagged as client"), ClientItem->GetBoolField(TEXT("isClient")));
				Test->TestFalse(TEXT("P0Pie client context is not flagged as server"), ClientItem->GetBoolField(TEXT("isServer")));
				Test->TestTrue(
					TEXT("P0Pie client and server use distinct PIE instances"),
					State->ClientInstanceId != State->ServerInstanceId);
			}
			else
			{
				Test->AddError(TEXT("P0Pie listing has no client world context item."));
			}

			// Bounded listing: one context only, truncation reported.
			TSharedRef<FJsonObject> CappedListing = BuildWorldContextsJson(1);
			Test->TestEqual(
				TEXT("P0Pie listing honors maxContexts=1"),
				static_cast<int32>(CappedListing->GetNumberField(TEXT("returnedCount"))),
				1);
			Test->TestTrue(
				TEXT("P0Pie listing reports truncation when the context cap applies"),
				CappedListing->GetBoolField(TEXT("truncated")));

			// ---- exact-world Actor inspection ------------------------------------
			UWorld* ServerWorld = nullptr;
			for (const FWorldContextSnapshot& Snapshot : ContextsFor(State->ServerInstanceId))
			{
				ServerWorld = Snapshot.World;
			}
			if (ServerWorld == nullptr)
			{
				Test->AddError(TEXT("P0Pie could not resolve the live server world for Actor inspection."));
				return;
			}

			FActorSpawnParameters SpawnParameters;
			SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
			const FVector SpawnLocation(123.5f, -45.25f, 67.75f);
			const FRotator SpawnRotation(0.0f, 90.0f, 0.0f);
			AStaticMeshActor* First = ServerWorld->SpawnActor<AStaticMeshActor>(
				AStaticMeshActor::StaticClass(),
				SpawnLocation,
				SpawnRotation,
				SpawnParameters);
			AStaticMeshActor* Second = ServerWorld->SpawnActor<AStaticMeshActor>(
				AStaticMeshActor::StaticClass(),
				SpawnLocation + FVector(200.0f, 0.0f, 0.0f),
				SpawnRotation,
				SpawnParameters);
			if (First == nullptr || Second == nullptr)
			{
				Test->AddError(TEXT("P0Pie could not spawn the two disposable PIE inspection Actor fixtures."));
				return;
			}

			UStaticMesh* Cube = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cube.Cube"));
			if (Cube != nullptr && First->GetStaticMeshComponent() != nullptr)
			{
				// A duplicated PIE Actor keeps its Static mobility, and a runtime
				// mesh assignment on a Static component is rejected by the engine.
				First->GetStaticMeshComponent()->SetMobility(EComponentMobility::Movable);
				First->GetStaticMeshComponent()->SetStaticMesh(Cube);
				State->CubeAssetPath = Cube->GetPathName();
				State->bComponentAssetVerified =
					First->GetStaticMeshComponent()->GetStaticMesh() == Cube;
			}
			const FString SharedLabel(TEXT("P0PieSharedLabel"));
			First->SetActorLabel(SharedLabel);
			Second->SetActorLabel(SharedLabel);
			State->SpawnedActorPath = First->GetPathName();
			State->SpawnedActorGuid = First->GetActorGuid().ToString(EGuidFormats::DigitsWithHyphensLower);
			State->SpawnedActorName = First->GetName();
			State->SpawnedActorLabel = First->GetActorLabel(false);
			State->bLabelAmbiguityVerified =
				First->GetActorLabel(false) == SharedLabel && Second->GetActorLabel(false) == SharedLabel;

			auto Inspect = [Test](
				const FActorInspectionRequest& Request,
				TSharedPtr<FJsonObject>& OutResult,
				FString& OutCode,
				FString& OutMessage)
			{
				const bool bOk = TryDescribePieActor(Request, OutResult, OutCode, OutMessage);
				if (!bOk && OutCode.IsEmpty())
				{
					Test->AddError(TEXT("P0Pie inspection failed without a stable error code."));
				}
				return bOk;
			};

			FActorInspectionRequest ByPath;
			ByPath.PieInstanceId = State->ServerInstanceId;
			ByPath.ActorPath = State->SpawnedActorPath;
			TSharedPtr<FJsonObject> ActorResult;
			FString Code;
			FString Message;
			if (Test->TestTrue(
				TEXT("P0Pie exact-world Actor inspection by actorPath succeeds"),
				Inspect(ByPath, ActorResult, Code, Message)))
			{
				State->ActorInspection = ActorResult;
				Test->TestEqual(
					TEXT("P0Pie inspection reports the exact PIE instance id"),
					static_cast<int32>(ActorResult->GetNumberField(TEXT("pieInstanceId"))),
					State->ServerInstanceId);
				Test->TestEqual(
					TEXT("P0Pie inspection reports the exact Actor path"),
					ActorResult->GetStringField(TEXT("actorPath")),
					State->SpawnedActorPath);
				Test->TestEqual(
					TEXT("P0Pie inspection reports the exact Actor class"),
					ActorResult->GetStringField(TEXT("classPath")),
					AStaticMeshActor::StaticClass()->GetPathName());
				Test->TestEqual(
					TEXT("P0Pie inspection reports the exact world path"),
					ActorResult->GetStringField(TEXT("worldPath")),
					State->ServerWorldPath);
				Test->TestEqual(
					TEXT("P0Pie inspection reports path-based resolution"),
					ActorResult->GetStringField(TEXT("resolvedBy")),
					FString(TEXT("actorPath")));
				Test->TestTrue(TEXT("P0Pie inspection is read-only"), ActorResult->GetBoolField(TEXT("readOnly")));
				Test->TestFalse(
					TEXT("P0Pie inspection does not need the database"),
					ActorResult->GetBoolField(TEXT("requiresDatabase")));

				const TSharedPtr<FJsonObject>* Transform = FindObjectField(ActorResult, TEXT("transform"));
				const TSharedPtr<FJsonObject>* Location = nullptr;
				if (Transform != nullptr)
				{
					Location = FindObjectField(*Transform, TEXT("location"));
				}
				if (Test->TestNotNull(TEXT("P0Pie inspection reports a transform location"), Location))
				{
					const float X = static_cast<float>((*Location)->GetNumberField(TEXT("x")));
					const float Y = static_cast<float>((*Location)->GetNumberField(TEXT("y")));
					const float Z = static_cast<float>((*Location)->GetNumberField(TEXT("z")));
					State->bTransformVerified =
						FMath::IsNearlyEqual(X, SpawnLocation.X, 0.05f)
						&& FMath::IsNearlyEqual(Y, SpawnLocation.Y, 0.05f)
						&& FMath::IsNearlyEqual(Z, SpawnLocation.Z, 0.05f);
					Test->TestTrue(TEXT("P0Pie inspection transform matches the spawned transform"), State->bTransformVerified);
				}

				const TSharedPtr<FJsonObject>* NetRole = FindObjectField(ActorResult, TEXT("netRole"));
				if (Test->TestNotNull(TEXT("P0Pie inspection reports NetRole"), NetRole))
				{
					Test->TestEqual(
						TEXT("P0Pie server Actor local role is authority"),
						(*NetRole)->GetStringField(TEXT("localRoleName")),
						FString(TEXT("authority")));
					Test->TestTrue(
						TEXT("P0Pie server Actor has authority"),
						(*NetRole)->GetBoolField(TEXT("hasAuthority")));
					Test->TestTrue(
						TEXT("P0Pie server Actor net role reports the server side"),
						(*NetRole)->GetBoolField(TEXT("isServer")));
					Test->TestFalse(
						TEXT("P0Pie server Actor net role is not the client side"),
						(*NetRole)->GetBoolField(TEXT("isClient")));
				}

				Test->TestTrue(
					TEXT("P0Pie inspection reports at least one component"),
					ActorResult->GetNumberField(TEXT("componentCount")) >= 1);
				const TArray<TSharedPtr<FJsonValue>>* Components = FindArrayField(ActorResult, TEXT("components"));
				if (Test->TestNotNull(TEXT("P0Pie inspection reports components"), Components))
				{
					const FString ExpectedComponentClass = UStaticMeshComponent::StaticClass()->GetPathName();
					bool bFoundMeshComponent = false;
					for (const TSharedPtr<FJsonValue>& Value : *Components)
					{
						TSharedPtr<FJsonObject> Component;
						if (Value.IsValid())
						{
							Component = Value->AsObject();
						}
						if (!Component.IsValid())
						{
							continue;
						}
						if (Component->GetStringField(TEXT("classPath")) == ExpectedComponentClass)
						{
							bFoundMeshComponent = true;
							const TArray<TSharedPtr<FJsonValue>>* Assets = FindArrayField(Component, TEXT("assets"));
							const bool bAssetPresent = !State->CubeAssetPath.IsEmpty()
								&& ArrayContainsString(Assets, State->CubeAssetPath);
							if (!State->CubeAssetPath.IsEmpty() && State->bComponentAssetVerified)
							{
								Test->TestTrue(
									TEXT("P0Pie inspection reports the bounded component asset path"),
									bAssetPresent);
							}
							break;
						}
					}
					Test->TestTrue(TEXT("P0Pie inspection reports the StaticMeshComponent class"), bFoundMeshComponent);
					if (!State->CubeAssetPath.IsEmpty())
					{
						Test->TestTrue(
							TEXT("P0Pie inspection reports the bounded component asset path"),
							State->bComponentAssetVerified);
					}
				}
			}
			else
			{
				Test->AddError(Code + TEXT(": ") + Message);
			}

			// Resolution by exact Actor GUID.
			FActorInspectionRequest ByGuid;
			ByGuid.PieInstanceId = State->ServerInstanceId;
			ByGuid.ActorGuid = State->SpawnedActorGuid.ToUpper();
			TSharedPtr<FJsonObject> GuidResult;
			if (Test->TestTrue(
				TEXT("P0Pie Actor inspection by actorGuid succeeds"),
				Inspect(ByGuid, GuidResult, Code, Message)))
			{
				Test->TestEqual(
					TEXT("P0Pie actorGuid resolution returns the exact Actor"),
					GuidResult->GetStringField(TEXT("actorPath")),
					State->SpawnedActorPath);
				Test->TestEqual(
					TEXT("P0Pie actorGuid resolution is reported"),
					GuidResult->GetStringField(TEXT("resolvedBy")),
					FString(TEXT("actorGuid")));
			}

			// Resolution by exact Actor name.
			FActorInspectionRequest ByName;
			ByName.PieInstanceId = State->ServerInstanceId;
			ByName.ActorName = State->SpawnedActorName;
			TSharedPtr<FJsonObject> NameResult;
			if (Test->TestTrue(
				TEXT("P0Pie Actor inspection by exact actorName succeeds"),
				Inspect(ByName, NameResult, Code, Message)))
			{
				Test->TestEqual(
					TEXT("P0Pie actorName resolution returns the exact Actor"),
					NameResult->GetStringField(TEXT("actorPath")),
					State->SpawnedActorPath);
				Test->TestEqual(
					TEXT("P0Pie actorName resolution is reported"),
					NameResult->GetStringField(TEXT("resolvedBy")),
					FString(TEXT("actorName")));
			}

			// Ambiguous label must be rejected with bounded candidates, not guessed.
			if (State->bLabelAmbiguityVerified)
			{
				FActorInspectionRequest ByLabel;
				ByLabel.PieInstanceId = State->ServerInstanceId;
				ByLabel.ActorLabel = SharedLabel;
				TSharedPtr<FJsonObject> AmbiguousResult;
				Test->TestFalse(
					TEXT("P0Pie ambiguous actorLabel is rejected"),
					Inspect(ByLabel, AmbiguousResult, Code, Message));
				Test->TestEqual(
					TEXT("P0Pie ambiguous Actor returns its own stable code"),
					Code,
					FString(UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::ActorAmbiguous)));
				Test->TestTrue(
					TEXT("P0Pie ambiguous Actor message bounds both candidates"),
					Message.Contains(First->GetPathName()) && Message.Contains(Second->GetPathName()));
			}
			else
			{
				Test->AddWarning(
					TEXT("P0Pie Actor labels could not be duplicated inside this PIE world; label ambiguity is covered by the offline contract tests."));
			}

			// Unknown Actor inside the exact world.
			FActorInspectionRequest UnknownActor;
			UnknownActor.PieInstanceId = State->ServerInstanceId;
			UnknownActor.ActorName = TEXT("P0PieDefinitelyMissingActor");
			TSharedPtr<FJsonObject> MissingActorResult;
			Test->TestFalse(
				TEXT("P0Pie unknown Actor is rejected"),
				Inspect(UnknownActor, MissingActorResult, Code, Message));
			Test->TestEqual(
				TEXT("P0Pie unknown Actor returns pie-actor-not-found"),
				Code,
				FString(UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::ActorNotFound)));
			Test->TestTrue(
				TEXT("P0Pie unknown Actor message names the exact world"),
				Message.Contains(State->ServerWorldPath));

			// Unknown instance while PIE is running: NOT_FOUND, not STALE.
			FActorInspectionRequest UnknownInstance;
			UnknownInstance.PieInstanceId = State->ServerInstanceId + 1000;
			UnknownInstance.ActorName = State->SpawnedActorName;
			TSharedPtr<FJsonObject> UnknownInstanceResult;
			Test->TestFalse(
				TEXT("P0Pie unknown live instance is rejected"),
				Inspect(UnknownInstance, UnknownInstanceResult, Code, Message));
			Test->TestEqual(
				TEXT("P0Pie unknown live instance returns pie-instance-not-found"),
				Code,
				FString(UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::InstanceNotFound)));

			// Reused world path: STALE, distinct from NOT_FOUND.
			FActorInspectionRequest WrongWorld;
			WrongWorld.PieInstanceId = State->ServerInstanceId;
			WrongWorld.ActorPath = State->SpawnedActorPath;
			WrongWorld.WorldPath = TEXT("/Game/P0PieNoSuchWorld.P0PieNoSuchWorld:PersistentLevel");
			TSharedPtr<FJsonObject> WrongWorldResult;
			Test->TestFalse(
				TEXT("P0Pie stale expected world is rejected"),
				Inspect(WrongWorld, WrongWorldResult, Code, Message));
			Test->TestEqual(
				TEXT("P0Pie stale expected world returns pie-world-stale"),
				Code,
				FString(UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::WorldStale)));
			Test->TestTrue(
				TEXT("P0Pie stale world failure is in the STALE family"),
				UEAgentKitPieRuntime::IsStaleFailure(UEAgentKitPieRuntime::EFailure::WorldStale));

			// Wrong expected class inside the same exact Actor: STALE.
			FActorInspectionRequest WrongClass;
			WrongClass.PieInstanceId = State->ServerInstanceId;
			WrongClass.ActorPath = State->SpawnedActorPath;
			WrongClass.ActorClassPath = UStaticMeshComponent::StaticClass()->GetPathName();
			TSharedPtr<FJsonObject> WrongClassResult;
			Test->TestFalse(
				TEXT("P0Pie stale expected Actor class is rejected"),
				Inspect(WrongClass, WrongClassResult, Code, Message));
			Test->TestEqual(
				TEXT("P0Pie stale expected class returns pie-actor-stale"),
				Code,
				FString(UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::ActorStale)));

			// Bounded component and asset caps.
			FActorInspectionRequest Bounded;
			Bounded.PieInstanceId = State->ServerInstanceId;
			Bounded.ActorPath = State->SpawnedActorPath;
			Bounded.MaxComponents = 1;
			Bounded.MaxComponentAssets = 1;
			TSharedPtr<FJsonObject> BoundedResult;
			if (Test->TestTrue(
				TEXT("P0Pie bounded Actor inspection succeeds"),
				Inspect(Bounded, BoundedResult, Code, Message)))
			{
				const TArray<TSharedPtr<FJsonValue>>* BoundedComponents =
					FindArrayField(BoundedResult, TEXT("components"));
				State->bBoundsVerified =
					BoundedResult->GetNumberField(TEXT("componentsReturned")) <= 1
					&& BoundedComponents != nullptr
					&& BoundedComponents->Num() <= 1;
				if (BoundedComponents != nullptr)
				{
					for (const TSharedPtr<FJsonValue>& Value : *BoundedComponents)
					{
						TSharedPtr<FJsonObject> Component;
						if (Value.IsValid())
						{
							Component = Value->AsObject();
						}
						const TArray<TSharedPtr<FJsonValue>>* Assets = Component.IsValid()
							? FindArrayField(Component, TEXT("assets"))
							: nullptr;
						if (Assets != nullptr && Assets->Num() > 1)
						{
							State->bBoundsVerified = false;
						}
					}
				}
				Test->TestTrue(
					TEXT("P0Pie component and asset caps are enforced"),
					State->bBoundsVerified);
				Test->TestEqual(
					TEXT("P0Pie bounded inspection reports the effective component cap"),
					static_cast<int32>(BoundedResult->GetNumberField(TEXT("maxComponents"))),
					1);
			}

			// A client world is addressed exactly too: an absent Actor must be
			// NOT_FOUND inside that client world, never resolved against the server.
			if (State->ClientInstanceId >= 0)
			{
				FActorInspectionRequest ClientAbsent;
				ClientAbsent.PieInstanceId = State->ClientInstanceId;
				ClientAbsent.ActorName = TEXT("P0PieDefinitelyMissingActor");
				TSharedPtr<FJsonObject> ClientAbsentResult;
				Test->TestFalse(
					TEXT("P0Pie client-world Actor lookup is rejected"),
					Inspect(ClientAbsent, ClientAbsentResult, Code, Message));
				Test->TestEqual(
					TEXT("P0Pie client-world lookup returns pie-actor-not-found"),
					Code,
					FString(UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::ActorNotFound)));
				State->bClientWorldVerified = Message.Contains(State->ClientWorldPath);
				Test->TestTrue(
					TEXT("P0Pie client-world lookup names the exact client world"),
					State->bClientWorldVerified);
			}

			// Stable code table for evidence.
			TSharedRef<FJsonObject> Codes = MakeShared<FJsonObject>();
			Codes->SetStringField(
				TEXT("notRunning"),
				UEAgentKitPieRuntime::LookupStatusCode(UEAgentKitPieRuntime::ELookupStatus::NotRunning));
			Codes->SetStringField(
				TEXT("notFound"),
				UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::InstanceNotFound));
			Codes->SetStringField(
				TEXT("stale"),
				UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::InstanceStale));
			Codes->SetStringField(
				TEXT("actorNotFound"),
				UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::ActorNotFound));
			Codes->SetStringField(
				TEXT("actorAmbiguous"),
				UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::ActorAmbiguous));
			Codes->SetStringField(
				TEXT("actorStale"),
				UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::ActorStale));
			Codes->SetStringField(
				TEXT("worldStale"),
				UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::WorldStale));
			Codes->SetStringField(
				TEXT("invalid"),
				UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::InvalidRequest));
			State->CodeEvidence = Codes;

			TSharedRef<FJsonObject> Bounds = MakeShared<FJsonObject>();
			Bounds->SetNumberField(TEXT("maxWorldContexts"), UEAgentKitPieRuntime::MaxPieWorldContexts);
			Bounds->SetNumberField(TEXT("maxActorComponents"), UEAgentKitPieRuntime::MaxActorComponents);
			Bounds->SetNumberField(TEXT("maxComponentAssets"), UEAgentKitPieRuntime::MaxComponentAssets);
			Bounds->SetNumberField(TEXT("maxScannedActors"), UEAgentKitPieRuntime::MaxScannedActors);
			Bounds->SetNumberField(TEXT("maxAmbiguousCandidates"), UEAgentKitPieRuntime::MaxAmbiguousCandidates);
			State->BoundsEvidence = Bounds;

			// Write the live-phase evidence before PIE is torn down so the run
			// always leaves proof even if the stop phase is interrupted.
			WriteRuntimeEvidence(State, TEXT("p0pie-native-upstream-evidence.json"), MakeShared<FJsonObject>());
		}

		static TArray<UEAgentKitPieRuntime::FWorldContextSnapshot> ContextsFor(int32 InstanceId)
		{
			TArray<UEAgentKitPieRuntime::FWorldContextSnapshot> Contexts;
			bool bTruncated = false;
			UEAgentKitPieRuntime::CollectPieWorldContexts(Contexts, bTruncated);
			Contexts.RemoveAll(
				[InstanceId](const UEAgentKitPieRuntime::FWorldContextSnapshot& Snapshot)
				{
					return Snapshot.PieInstanceId != InstanceId || Snapshot.World == nullptr;
				});
			return Contexts;
		}

		TSharedRef<FRuntimeState> State;
	};

	/**
	 * Keep the live PIE session open for a moment after the in-process checks so
	 * an external read-only probe can observe the exact same session over the
	 * Editor Bridge.  Bounded and purely time based; it writes nothing.
	 */
	class FDwellBeforeEndingPie final : public IAutomationLatentCommand
	{
	public:
		explicit FDwellBeforeEndingPie(const double InSeconds)
			: Seconds(InSeconds)
		{
		}

		virtual bool Update() override
		{
			if (StartedAt <= 0.0)
			{
				StartedAt = FPlatformTime::Seconds();
			}
			return FPlatformTime::Seconds() - StartedAt >= Seconds;
		}

	private:
		double Seconds = 0.0;
		double StartedAt = 0.0;
	};

	class FVerifyAfterPieStop final : public IAutomationLatentCommand
	{
	public:
		explicit FVerifyAfterPieStop(TSharedRef<FRuntimeState> InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			if (State->bStopPhaseCompleted)
			{
				return true;
			}
			if (State->StopPhaseStartedAt <= 0.0)
			{
				State->StopPhaseStartedAt = FPlatformTime::Seconds();
			}
			TArray<UEAgentKitPieRuntime::FWorldContextSnapshot> Contexts;
			bool bTruncated = false;
			UEAgentKitPieRuntime::CollectPieWorldContexts(Contexts, bTruncated);
			if (Contexts.Num() > 0)
			{
				if (FPlatformTime::Seconds() - State->StopPhaseStartedAt > 60.0)
				{
					State->Test->AddError(TEXT("PIE did not stop within 60 seconds after the inspection checks."));
					State->bStopPhaseCompleted = true;
					RestorePlaySettings(State);
					return true;
				}
				return false;
			}

			FAutomationTestBase* Test = State->Test;
			using namespace UEAgentKitPieRuntime;
			TSharedRef<FJsonObject> Listing = UEAgentKitPieRuntime::BuildWorldContextsJson(16);
			Test->TestFalse(TEXT("P0Pie listing reports stopped after PIE ends"), Listing->GetBoolField(TEXT("running")));
			Test->TestEqual(
				TEXT("P0Pie stopped listing returns pie-not-running"),
				Listing->GetStringField(TEXT("code")),
				FString(UEAgentKitPieRuntime::LookupStatusCode(UEAgentKitPieRuntime::ELookupStatus::NotRunning)));

			FActorInspectionRequest Stale;
			Stale.PieInstanceId = State->ServerInstanceId;
			Stale.ActorPath = State->SpawnedActorPath;
			TSharedPtr<FJsonObject> StaleResult;
			FString Code;
			FString Message;
			if (State->ServerInstanceId >= 0 && !State->SpawnedActorPath.IsEmpty())
			{
				const bool bOk = UEAgentKitPieRuntime::TryDescribePieActor(Stale, StaleResult, Code, Message);
				Test->TestFalse(TEXT("P0Pie ended instance inspection is rejected"), bOk);
				Test->TestEqual(
					TEXT("P0Pie ended instance returns pie-instance-stale"),
					Code,
					FString(UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::InstanceStale)));
			}

			// Distinct NOT_RUNNING for an instance that was never observed.
			FActorInspectionRequest NeverObserved;
			NeverObserved.PieInstanceId = 9001;
			NeverObserved.ActorName = TEXT("P0PieNeverObservedActor");
			TSharedPtr<FJsonObject> NeverObservedResult;
			const bool bNeverOk = UEAgentKitPieRuntime::TryDescribePieActor(
				NeverObserved,
				NeverObservedResult,
				Code,
				Message);
			Test->TestFalse(TEXT("P0Pie never-observed instance inspection is rejected"), bNeverOk);
			Test->TestEqual(
				TEXT("P0Pie never-observed instance returns pie-not-running"),
				Code,
				FString(UEAgentKitPieRuntime::FailureCode(UEAgentKitPieRuntime::EFailure::InstanceNotRunning)));

			// Evidence: what was really observed in this native run.
			TSharedRef<FJsonObject> Extra = MakeShared<FJsonObject>();
			Extra->SetBoolField(TEXT("stoppedListingVerified"), true);
			Extra->SetBoolField(
				TEXT("staleInstanceVerified"),
				State->ServerInstanceId >= 0 && !State->SpawnedActorPath.IsEmpty());
			Extra->SetBoolField(TEXT("notRunningVerified"), true);
			UEAgentKitP0PieRuntime::WriteRuntimeEvidence(
				State,
				TEXT("p0pie-native-stopped-evidence.json"),
				Extra);

			RestorePlaySettings(State);
			State->bStopPhaseCompleted = true;
			return true;
		}

	private:
		TSharedRef<FRuntimeState> State;
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitP0PieContractTest,
	"UEAgentKit.P0Pie.ContractCodes",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitP0PieContractTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPieRuntime;

	ResetPieInstanceHistoryForTests();
	const TArray<int32> LiveInstanceIds = {1, 2};
	const TArray<int32> RetiredInstanceIds = {3};
	const TArray<int32> NoInstanceIds;

	TestEqual(
		TEXT("P0Pie live instance matches"),
		static_cast<int32>(ClassifyInstanceLookup(1, LiveInstanceIds, RetiredInstanceIds)),
		static_cast<int32>(ELookupStatus::Matched));
	TestEqual(
		TEXT("P0Pie retired instance is stale while PIE still runs"),
		static_cast<int32>(ClassifyInstanceLookup(3, LiveInstanceIds, RetiredInstanceIds)),
		static_cast<int32>(ELookupStatus::Stale));
	TestEqual(
		TEXT("P0Pie unknown instance is not-found while PIE runs"),
		static_cast<int32>(ClassifyInstanceLookup(4, LiveInstanceIds, RetiredInstanceIds)),
		static_cast<int32>(ELookupStatus::NotFound));
	TestEqual(
		TEXT("P0Pie no live instance and no history is not-running"),
		static_cast<int32>(ClassifyInstanceLookup(4, NoInstanceIds, NoInstanceIds)),
		static_cast<int32>(ELookupStatus::NotRunning));
	TestEqual(
		TEXT("P0Pie retired instance stays stale when PIE stops"),
		static_cast<int32>(ClassifyInstanceLookup(3, NoInstanceIds, RetiredInstanceIds)),
		static_cast<int32>(ELookupStatus::Stale));

	const FString NotRunningCode(LookupStatusCode(ELookupStatus::NotRunning));
	const FString NotFoundCode(FailureCode(EFailure::InstanceNotFound));
	const FString StaleCode(FailureCode(EFailure::InstanceStale));
	TestEqual(TEXT("P0Pie not-running wire code"), NotRunningCode, FString(TEXT("pie-not-running")));
	TestEqual(TEXT("P0Pie not-found wire code"), NotFoundCode, FString(TEXT("pie-instance-not-found")));
	TestEqual(TEXT("P0Pie stale wire code"), StaleCode, FString(TEXT("pie-instance-stale")));
	TestTrue(
		TEXT("P0Pie NOT_RUNNING, NOT_FOUND and STALE codes are distinct"),
		NotRunningCode != NotFoundCode && NotFoundCode != StaleCode && NotRunningCode != StaleCode);
	TestTrue(TEXT("P0Pie world-stale belongs to the STALE family"), IsStaleFailure(EFailure::WorldStale));
	TestTrue(TEXT("P0Pie actor-stale belongs to the STALE family"), IsStaleFailure(EFailure::ActorStale));
	TestFalse(TEXT("P0Pie not-found is not STALE"), IsStaleFailure(EFailure::InstanceNotFound));

	TSharedRef<FJsonObject> StoppedListing = BuildWorldContextsJson(16);
	TestFalse(TEXT("P0Pie stopped listing is not running"), StoppedListing->GetBoolField(TEXT("running")));
	TestEqual(
		TEXT("P0Pie stopped listing state"),
		StoppedListing->GetStringField(TEXT("state")),
		FString(TEXT("stopped")));
	TestEqual(
		TEXT("P0Pie stopped listing code"),
		StoppedListing->GetStringField(TEXT("code")),
		NotRunningCode);
	TestEqual(
		TEXT("P0Pie stopped listing has no context items"),
		static_cast<int32>(StoppedListing->GetArrayField(TEXT("items")).Num()),
		0);
	TestTrue(
		TEXT("P0Pie listing clamps maxContexts above the hard cap"),
		BuildWorldContextsJson(4096)->GetNumberField(TEXT("maxContexts")) <= MaxPieWorldContexts);

	FActorInspectionRequest TwoIdentities;
	TwoIdentities.PieInstanceId = 1;
	TwoIdentities.ActorName = TEXT("P0PieName");
	TwoIdentities.ActorLabel = TEXT("P0PieLabel");
	TSharedPtr<FJsonObject> Result;
	FString Code;
	FString Message;
	TestFalse(
		TEXT("P0Pie two Actor identities are rejected"),
		TryDescribePieActor(TwoIdentities, Result, Code, Message));
	TestEqual(TEXT("P0Pie two identities return pie-invalid-parameters"), Code, FString(TEXT("pie-invalid-parameters")));

	FActorInspectionRequest NoIdentity;
	NoIdentity.PieInstanceId = 1;
	TestFalse(
		TEXT("P0Pie missing Actor identity is rejected"),
		TryDescribePieActor(NoIdentity, Result, Code, Message));
	TestEqual(TEXT("P0Pie missing identity returns pie-invalid-parameters"), Code, FString(TEXT("pie-invalid-parameters")));

	FActorInspectionRequest BadInstance;
	BadInstance.PieInstanceId = -1;
	BadInstance.ActorName = TEXT("P0PieName");
	TestFalse(
		TEXT("P0Pie invalid PIE instance id is rejected"),
		TryDescribePieActor(BadInstance, Result, Code, Message));
	TestEqual(TEXT("P0Pie invalid instance returns pie-invalid-parameters"), Code, FString(TEXT("pie-invalid-parameters")));

	FActorInspectionRequest NotRunning;
	NotRunning.PieInstanceId = 1;
	NotRunning.ActorName = TEXT("P0PieName");
	TestFalse(
		TEXT("P0Pie Actor inspection without PIE is rejected"),
		TryDescribePieActor(NotRunning, Result, Code, Message));
	TestEqual(TEXT("P0Pie Actor inspection without PIE returns pie-not-running"), Code, NotRunningCode);

	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitP0PieRuntimeTest,
	"UEAgentKit.P0Pie.RuntimeWorldContexts",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitP0PieRuntimeTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitP0PieRuntime;

	TSharedRef<FRuntimeState> State = MakeShared<FRuntimeState>();
	State->Test = this;

	ULevelEditorPlaySettings* PlaySettings = GetMutableDefault<ULevelEditorPlaySettings>();
	if (!TestNotNull(TEXT("P0Pie PIE play settings are available"), PlaySettings))
	{
		return false;
	}
	EPlayNetMode OriginalPlayNetMode = PIE_Standalone;
	PlaySettings->GetPlayNetMode(OriginalPlayNetMode);
	State->OriginalPlayNetMode = OriginalPlayNetMode;
	int32 OriginalNumberOfClients = 1;
	PlaySettings->GetPlayNumberOfClients(OriginalNumberOfClients);
	State->OriginalPlayNumberOfClients = OriginalNumberOfClients;
	bool bOriginalRunUnderOneProcess = false;
	PlaySettings->GetRunUnderOneProcess(bOriginalRunUnderOneProcess);
	State->bOriginalRunUnderOneProcess = bOriginalRunUnderOneProcess;
	State->bOriginalLaunchSeparateServer = PlaySettings->bLaunchSeparateServer;
	State->bSettingsCaptured = true;
	PlaySettings->SetPlayNetMode(PIE_ListenServer);
	PlaySettings->SetPlayNumberOfClients(2);
	PlaySettings->SetRunUnderOneProcess(true);
	PlaySettings->bLaunchSeparateServer = false;

	ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
	ADD_LATENT_AUTOMATION_COMMAND(FWaitForPieContexts(State));
	ADD_LATENT_AUTOMATION_COMMAND(FDwellBeforeEndingPie(4.0));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	ADD_LATENT_AUTOMATION_COMMAND(FVerifyAfterPieStop(State));
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS

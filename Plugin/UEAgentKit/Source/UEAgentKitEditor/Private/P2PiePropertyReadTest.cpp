// P2 native evidence for the read-only PIE reflective property surface.
//
// Two independent pieces of evidence:
//
//  * `UEAgentKit.P0Pie.PropertyContract` runs with PIE stopped and proves the
//    pure reflection classification, the support verdicts, the wire codes and
//    the request validation of both P2 methods.
//  * `UEAgentKit.P0Pie.PropertyReadRuntime` starts one disposable listen-server
//    PIE session, spawns the P2 fixture Actor in the server world and proves
//    real values: scalars, expanded structs, paged containers, object-identity
//    references, explicit unsupported-type refusals and Server/Client isolation.
//
// Nothing here writes an asset, saves a package or opens the SQLite index; the
// fixture Actor lives only inside the disposable PIE world.

#include "PieObjectProperties.h"
#include "PieP2PropertyFixture.h"

#include "Components/ActorComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Editor.h"
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
#include "UObject/UnrealType.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace UEAgentKitP2PropertyRuntime
{
	const TCHAR* const EvidenceRelativeDirectory = TEXT("PieP2PropertyRead");

	TSharedPtr<FJsonObject> ItemNamed(const TSharedPtr<FJsonObject>& Result, const FString& Name)
	{
		const TArray<TSharedPtr<FJsonValue>>* Items = nullptr;
		if (!Result.IsValid() || !Result->TryGetArrayField(TEXT("properties"), Items) || Items == nullptr)
		{
			return nullptr;
		}
		for (const TSharedPtr<FJsonValue>& Value : *Items)
		{
			TSharedPtr<FJsonObject> Item;
			if (Value.IsValid())
			{
				Item = Value->AsObject();
			}
			FString ItemName;
			if (Item.IsValid() && Item->TryGetStringField(TEXT("name"), ItemName) && ItemName == Name)
			{
				return Item;
			}
		}
		return nullptr;
	}

	FString ItemString(const TSharedPtr<FJsonObject>& Item, const TCHAR* Field)
	{
		FString Value;
		return Item.IsValid() && Item->TryGetStringField(Field, Value) ? Value : FString();
	}

	bool ItemBool(const TSharedPtr<FJsonObject>& Item, const TCHAR* Field)
	{
		bool Value = false;
		return Item.IsValid() && Item->TryGetBoolField(Field, Value) ? Value : false;
	}

	double ItemNumber(const TSharedPtr<FJsonObject>& Item, const TCHAR* Field)
	{
		double Value = 0.0;
		return Item.IsValid() && Item->TryGetNumberField(Field, Value) ? Value : 0.0;
	}

	bool ItemHasField(const TSharedPtr<FJsonObject>& Item, const TCHAR* Field)
	{
		return Item.IsValid() && Item->HasField(Field);
	}

	const TArray<TSharedPtr<FJsonValue>>* ItemArray(const TSharedPtr<FJsonObject>& Item, const TCHAR* Field)
	{
		const TArray<TSharedPtr<FJsonValue>>* Values = nullptr;
		if (!Item.IsValid() || !Item->TryGetArrayField(Field, Values))
		{
			return nullptr;
		}
		return Values;
	}

	/** One member of a struct-valued entry. */
	TSharedPtr<FJsonObject> MemberNamed(const TSharedPtr<FJsonObject>& Item, const FString& Name)
	{
		const TArray<TSharedPtr<FJsonValue>>* Members = ItemArray(Item, TEXT("members"));
		if (Members == nullptr)
		{
			return nullptr;
		}
		for (const TSharedPtr<FJsonValue>& Value : *Members)
		{
			TSharedPtr<FJsonObject> Member;
			if (Value.IsValid())
			{
				Member = Value->AsObject();
			}
			FString MemberName;
			if (Member.IsValid() && Member->TryGetStringField(TEXT("name"), MemberName) && MemberName == Name)
			{
				return Member;
			}
		}
		return nullptr;
	}

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

	struct FState
	{
		FAutomationTestBase* Test = nullptr;

		TEnumAsByte<EPlayNetMode> OriginalPlayNetMode = PIE_Standalone;
		int32 OriginalPlayNumberOfClients = 1;
		bool bOriginalRunUnderOneProcess = false;
		bool bOriginalLaunchSeparateServer = false;
		bool bSettingsCaptured = false;
		bool bSettingsRestored = false;

		int32 ServerInstanceId = INDEX_NONE;
		int32 ClientInstanceId = INDEX_NONE;
		FString ServerContextId;
		FString ClientContextId;
		FString ServerWorldPath;
		int32 ContextCount = 0;

		FString FixtureActorName;
		FString FixtureActorPath;
		FString NullRefActorName;
		FString MeshComponentName;
		FString MeshComponentPath;

		bool bCompleted = false;
		double StartedAt = 0.0;
		double ReadySince = 0.0;
	};

	void RestorePlaySettings(const TSharedRef<FState>& State)
	{
		if (!State->bSettingsCaptured || State->bSettingsRestored)
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
		State->bSettingsRestored = true;
	}

	void WriteEvidence(
		const TSharedRef<FState>& State,
		const TSharedRef<FJsonObject>& ExtraFields)
	{
		TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
		Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p2pie.property-read/1.0"));
		Evidence->SetNumberField(TEXT("processId"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
		Evidence->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
		Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
		Evidence->SetStringField(TEXT("source"), TEXT("native-automation-real-pie"));
		Evidence->SetNumberField(TEXT("pieContextCount"), State->ContextCount);
		Evidence->SetNumberField(TEXT("serverPieInstanceId"), State->ServerInstanceId);
		Evidence->SetNumberField(TEXT("clientPieInstanceId"), State->ClientInstanceId);
		Evidence->SetStringField(TEXT("serverContextId"), State->ServerContextId);
		Evidence->SetStringField(TEXT("serverWorldPath"), State->ServerWorldPath);
		Evidence->SetStringField(TEXT("fixtureActorPath"), State->FixtureActorPath);
		Evidence->SetStringField(TEXT("fixtureActorName"), State->FixtureActorName);
		Evidence->SetStringField(TEXT("meshComponentPath"), State->MeshComponentPath);
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Field : ExtraFields->Values)
		{
			Evidence->SetField(Field.Key, Field.Value);
		}

		const FString Path = FPaths::Combine(
			FPaths::ProjectSavedDir(),
			EvidenceRelativeDirectory,
			TEXT("p2pie-property-read-evidence.json"));
		const bool bWritten = WriteJson(Path, Evidence);
		State->Test->TestTrue(
			TEXT("P2Pie property-read evidence is written"),
			bWritten);
	}

	/** Bounded, purely time based dwell; it writes nothing and reads nothing. */
	class FDwellBeforeStopping final : public IAutomationLatentCommand
	{
	public:
		explicit FDwellBeforeStopping(const double InSeconds)
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

	class FWaitForPropertyContexts final : public IAutomationLatentCommand	{
	public:
		explicit FWaitForPropertyContexts(TSharedRef<FState> InState)
			: State(InState)
		{
		}

		virtual bool Update() override
		{
			if (State->bCompleted)
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
					RunChecks(Contexts);
					State->bCompleted = true;
					return true;
				}
			}
			if (Now - State->StartedAt > 180.0)
			{
				State->Test->AddError(
					TEXT("PIE did not produce at least two live PIE world contexts (server + client) within 180 seconds."));
				State->bCompleted = true;
				RestorePlaySettings(State);
				return true;
			}
			return false;
		}

	private:
		void RunChecks(const TArray<UEAgentKitPieRuntime::FWorldContextSnapshot>& Contexts)
		{
			using namespace UEAgentKitPieRuntime;
			FAutomationTestBase* Test = State->Test;
			State->ContextCount = Contexts.Num();

			const FWorldContextSnapshot* Server = nullptr;
			const FWorldContextSnapshot* Client = nullptr;
			for (const FWorldContextSnapshot& Candidate : Contexts)
			{
				if (Server == nullptr && Candidate.bIsServer)
				{
					Server = &Candidate;
				}
				else if (Client == nullptr && Candidate.bIsClient)
				{
					Client = &Candidate;
				}
			}
			if (!Test->TestNotNull(TEXT("P2Pie server world context is available"), Server))
			{
				return;
			}
			if (!Test->TestNotNull(TEXT("P2Pie client world context is available"), Client))
			{
				return;
			}
			State->ServerInstanceId = Server->PieInstanceId;
			State->ServerContextId = Server->ContextId;
			State->ServerWorldPath = Server->WorldPath;
			State->ClientInstanceId = Client->PieInstanceId;
			State->ClientContextId = Client->ContextId;
			UWorld* ServerWorld = Server->World;
			if (!Test->TestNotNull(TEXT("P2Pie server world is resolvable"), ServerWorld))
			{
				return;
			}

			// ---- fixture -----------------------------------------------------
			FActorSpawnParameters SpawnParameters;
			SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
			SpawnParameters.Name = FName(TEXT("PieP2PropertyFixture"));
			APieP2PropertyFixtureActor* Fixture = ServerWorld->SpawnActor<APieP2PropertyFixtureActor>(
				APieP2PropertyFixtureActor::StaticClass(),
				FVector(500.0f, 100.0f, 50.0f),
				FRotator::ZeroRotator,
				SpawnParameters);
			if (!Test->TestNotNull(TEXT("P2Pie property fixture Actor spawns"), Fixture))
			{
				RestorePlaySettings(State);
				return;
			}
			State->FixtureActorName = Fixture->GetName();
			State->FixtureActorPath = Fixture->GetPathName();
			Fixture->ObjectRefValue = Fixture->Mesh;
			State->MeshComponentName = Fixture->Mesh != nullptr ? Fixture->Mesh->GetName() : FString();
			State->MeshComponentPath = Fixture->Mesh != nullptr ? Fixture->Mesh->GetPathName() : FString();

			FActorSpawnParameters NullRefSpawnParameters;
			NullRefSpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
			NullRefSpawnParameters.Name = FName(TEXT("PieP2NullRefFixture"));
			APieP2PropertyFixtureActor* NullRefFixture =
				ServerWorld->SpawnActor<APieP2PropertyFixtureActor>(
					APieP2PropertyFixtureActor::StaticClass(),
					FVector(-500.0f, 100.0f, 50.0f),
					FRotator::ZeroRotator,
					NullRefSpawnParameters);
			if (!Test->TestNotNull(TEXT("P2Pie null-reference fixture Actor spawns"), NullRefFixture))
			{
				RestorePlaySettings(State);
				return;
			}
			State->NullRefActorName = NullRefFixture->GetName();

			// ---- property discovery on the Actor ------------------------------
			FObjectPropertyListRequest ActorListRequest;
			ActorListRequest.Target.Actor.PieInstanceId = State->ServerInstanceId;
			ActorListRequest.Target.Actor.ActorPath = State->FixtureActorPath;
			ActorListRequest.MaxProperties = 256;

			TSharedPtr<FJsonObject> ActorList;
			FString Code;
			FString Message;
			bool bDiscoveryVerified = false;
			bool bNoVisibilityGateVerified = false;
			bool bContainerFlagsVerified = false;
			TSharedPtr<FJsonObject> DiscoveryEvidence;
			if (Test->TestTrue(
				TEXT("P2Pie Actor property discovery succeeds"),
				TryListPieObjectProperties(ActorListRequest, ActorList, Code, Message)))
			{
				Test->TestEqual(
					TEXT("P2Pie discovery source is pie-live"),
					ActorList->GetStringField(TEXT("source")),
					FString(TEXT("pie-live")));
				Test->TestTrue(
					TEXT("P2Pie discovery is read-only"),
					ActorList->GetBoolField(TEXT("readOnly")));
				Test->TestFalse(
					TEXT("P2Pie discovery does not need the database"),
					ActorList->GetBoolField(TEXT("requiresDatabase")));
				Test->TestEqual(
					TEXT("P2Pie discovery reports the exact world key"),
					ActorList->GetStringField(TEXT("worldKey")),
					State->ServerContextId);
				Test->TestEqual(
					TEXT("P2Pie discovery reports the exact instance"),
					static_cast<int32>(ActorList->GetNumberField(TEXT("pieInstanceId"))),
					State->ServerInstanceId);
				Test->TestEqual(
					TEXT("P2Pie discovery targets the Actor"),
					ActorList->GetStringField(TEXT("targetKind")),
					FString(TEXT("actor")));
				Test->TestEqual(
					TEXT("P2Pie discovery reports the exact object path"),
					ActorList->GetStringField(TEXT("objectPath")),
					State->FixtureActorPath);
				Test->TestEqual(
					TEXT("P2Pie discovery reports the exact class path"),
					ActorList->GetStringField(TEXT("classPath")),
					Fixture->GetClass()->GetPathName());
				Test->TestEqual(
					TEXT("P2Pie discovery reports every reflected property"),
					static_cast<int32>(ActorList->GetNumberField(TEXT("totalCount"))),
					static_cast<int32>(ActorList->GetNumberField(TEXT("matchedCount"))));
				Test->TestFalse(
					TEXT("P2Pie unfiltered discovery is not truncated"),
					ActorList->GetBoolField(TEXT("truncated")));

				const TSharedPtr<FJsonObject> Health = ItemNamed(ActorList, TEXT("Health"));
				Test->TestEqual(
					TEXT("P2Pie Health descriptor kind"),
					ItemString(Health, TEXT("typeKind")),
					FString(TEXT("float")));
				Test->TestTrue(TEXT("P2Pie Health descriptor is supported"), ItemBool(Health, TEXT("supported")));

				const TSharedPtr<FJsonObject> IntArray = ItemNamed(ActorList, TEXT("IntArray"));
				Test->TestEqual(
					TEXT("P2Pie IntArray descriptor kind"),
					ItemString(IntArray, TEXT("typeKind")),
					FString(TEXT("array")));
				Test->TestTrue(TEXT("P2Pie IntArray is flagged as an array"), ItemBool(IntArray, TEXT("isArray")));
				const TSharedPtr<FJsonObject> NameToIntMap = ItemNamed(ActorList, TEXT("NameToIntMap"));
				Test->TestEqual(
					TEXT("P2Pie NameToIntMap descriptor kind"),
					ItemString(NameToIntMap, TEXT("typeKind")),
					FString(TEXT("map")));
				Test->TestTrue(TEXT("P2Pie NameToIntMap is flagged as a map"), ItemBool(NameToIntMap, TEXT("isMap")));
				const TSharedPtr<FJsonObject> IntSet = ItemNamed(ActorList, TEXT("IntSet"));
				Test->TestEqual(
					TEXT("P2Pie IntSet descriptor kind"),
					ItemString(IntSet, TEXT("typeKind")),
					FString(TEXT("set")));
				Test->TestTrue(TEXT("P2Pie IntSet is flagged as a set"), ItemBool(IntSet, TEXT("isSet")));
				const TSharedPtr<FJsonObject> Vector = ItemNamed(ActorList, TEXT("VectorValue"));
				Test->TestEqual(
					TEXT("P2Pie VectorValue descriptor kind"),
					ItemString(Vector, TEXT("typeKind")),
					FString(TEXT("struct")));
				Test->TestTrue(TEXT("P2Pie VectorValue is flagged as a struct"), ItemBool(Vector, TEXT("isStruct")));
				const TSharedPtr<FJsonObject> ObjectRef = ItemNamed(ActorList, TEXT("ObjectRefValue"));
				Test->TestTrue(
					TEXT("P2Pie ObjectRefValue is flagged as an object reference"),
					ItemBool(ObjectRef, TEXT("isObjectRef")));
				const TSharedPtr<FJsonObject> Mode = ItemNamed(ActorList, TEXT("Mode"));
				Test->TestEqual(
					TEXT("P2Pie Mode descriptor kind"),
					ItemString(Mode, TEXT("typeKind")),
					FString(TEXT("enum")));

				bContainerFlagsVerified = IntArray.IsValid() && NameToIntMap.IsValid() && IntSet.IsValid()
					&& Vector.IsValid() && ObjectRef.IsValid();

				// Refused shapes name their reason instead of hiding.
				const TSharedPtr<FJsonObject> Text = ItemNamed(ActorList, TEXT("TextValue"));
				Test->TestFalse(TEXT("P2Pie FText is not supported"), ItemBool(Text, TEXT("supported")));
				Test->TestEqual(
					TEXT("P2Pie FText refusal reason"),
					ItemString(Text, TEXT("unsupportedReason")),
					FString(TEXT("text-not-readable")));
				const TSharedPtr<FJsonObject> Delegate = ItemNamed(ActorList, TEXT("DelegateValue"));
				Test->TestEqual(
					TEXT("P2Pie delegate refusal reason"),
					ItemString(Delegate, TEXT("unsupportedReason")),
					FString(TEXT("delegate-not-readable")));
				const TSharedPtr<FJsonObject> Multicast = ItemNamed(ActorList, TEXT("MulticastValue"));
				Test->TestEqual(
					TEXT("P2Pie multicast delegate refusal reason"),
					ItemString(Multicast, TEXT("unsupportedReason")),
					FString(TEXT("delegate-not-readable")));
				const TSharedPtr<FJsonObject> Bitfield = ItemNamed(ActorList, TEXT("bBitfieldFlag"));
				Test->TestEqual(
					TEXT("P2Pie bitfield refusal reason"),
					ItemString(Bitfield, TEXT("unsupportedReason")),
					FString(TEXT("bitfield-not-readable")));

				// Reflection driven: a property that is neither Edit nor
				// BlueprintVisible is still discovered and still readable.
				const TSharedPtr<FJsonObject> NotExposed = ItemNamed(ActorList, TEXT("NotExposed"));
				Test->TestTrue(
					TEXT("P2Pie discovery does not gate on Edit/BlueprintVisible"),
					ItemBool(NotExposed, TEXT("supported")));
				bNoVisibilityGateVerified = NotExposed.IsValid();

				bDiscoveryVerified = Health.IsValid() && IntArray.IsValid() && Vector.IsValid()
					&& Text.IsValid() && Bitfield.IsValid();

				DiscoveryEvidence = ActorList;
			}
			else
			{
				Test->AddError(FString::Printf(TEXT("P2Pie Actor property discovery failed: %s %s"), *Code, *Message));
			}

			// ---- name filter ---------------------------------------------------
			if (ActorList.IsValid())
			{
				FObjectPropertyListRequest Filtered = ActorListRequest;
				Filtered.NameContains = TEXT("vector");
				Filtered.MaxProperties = 8;
				TSharedPtr<FJsonObject> FilteredResult;
				if (Test->TestTrue(
					TEXT("P2Pie filtered discovery succeeds"),
					TryListPieObjectProperties(Filtered, FilteredResult, Code, Message)))
				{
					const TArray<TSharedPtr<FJsonValue>>* FilteredItems =
						ItemArray(FilteredResult, TEXT("properties"));
					Test->TestTrue(
						TEXT("P2Pie name filter still matches VectorValue"),
						ItemNamed(FilteredResult, TEXT("VectorValue")).IsValid());
					Test->TestFalse(
						TEXT("P2Pie name filter drops non-matching properties"),
						ItemNamed(FilteredResult, TEXT("Health")).IsValid());
					Test->TestTrue(
						TEXT("P2Pie filtered discovery narrows matchedCount"),
						FilteredResult->GetNumberField(TEXT("matchedCount"))
							< ActorList->GetNumberField(TEXT("totalCount")));
					bool bAllNamesMatch = true;
					if (FilteredItems != nullptr)
					{
						for (const TSharedPtr<FJsonValue>& Value : *FilteredItems)
						{
							TSharedPtr<FJsonObject> Item;
							if (Value.IsValid())
							{
								Item = Value->AsObject();
							}
							if (!ItemString(Item, TEXT("name")).Contains(TEXT("vector"), ESearchCase::IgnoreCase))
							{
								bAllNamesMatch = false;
							}
						}
					}
					Test->TestTrue(TEXT("P2Pie every filtered name matches ignoring case"), bAllNamesMatch);
				}
			}

			// ---- property discovery on the StaticMeshComponent ----------------
			bool bComponentDiscoveryVerified = false;
			TSharedPtr<FJsonObject> ComponentEvidence;
			FObjectPropertyListRequest ComponentListRequest;
			ComponentListRequest.Target.Actor.PieInstanceId = State->ServerInstanceId;
			ComponentListRequest.Target.Actor.ActorPath = State->FixtureActorPath;
			ComponentListRequest.Target.ComponentName = State->MeshComponentName;
			ComponentListRequest.Target.ComponentClassPath =
				UStaticMeshComponent::StaticClass()->GetPathName();
			ComponentListRequest.MaxProperties = 512;
			TSharedPtr<FJsonObject> ComponentList;
			if (Test->TestTrue(
				TEXT("P2Pie StaticMeshComponent property discovery succeeds"),
				TryListPieObjectProperties(ComponentListRequest, ComponentList, Code, Message)))
			{
				Test->TestEqual(
					TEXT("P2Pie component discovery targets the Component"),
					ComponentList->GetStringField(TEXT("targetKind")),
					FString(TEXT("component")));
				Test->TestEqual(
					TEXT("P2Pie component discovery reports the exact object path"),
					ComponentList->GetStringField(TEXT("objectPath")),
					State->MeshComponentPath);
				Test->TestEqual(
					TEXT("P2Pie component discovery reports the owner Actor"),
					ComponentList->GetStringField(TEXT("actorPath")),
					State->FixtureActorPath);

				const TSharedPtr<FJsonObject> CastShadow = ItemNamed(ComponentList, TEXT("CastShadow"));
				Test->TestEqual(
					TEXT("P2Pie StaticMeshComponent CastShadow is a bitfield"),
					ItemString(CastShadow, TEXT("typeKind")),
					FString(TEXT("bitfield")));
				const TSharedPtr<FJsonObject> Mobility = ItemNamed(ComponentList, TEXT("Mobility"));
				Test->TestEqual(
					TEXT("P2Pie StaticMeshComponent Mobility is an enum"),
					ItemString(Mobility, TEXT("typeKind")),
					FString(TEXT("enum")));
				const TSharedPtr<FJsonObject> OverrideMaterials =
					ItemNamed(ComponentList, TEXT("OverrideMaterials"));
				Test->TestEqual(
					TEXT("P2Pie StaticMeshComponent OverrideMaterials is an array"),
					ItemString(OverrideMaterials, TEXT("typeKind")),
					FString(TEXT("array")));
				const TSharedPtr<FJsonObject> StaticMesh = ItemNamed(ComponentList, TEXT("StaticMesh"));
				Test->TestTrue(
					TEXT("P2Pie StaticMeshComponent StaticMesh is an object reference"),
					ItemBool(StaticMesh, TEXT("isObjectRef")));
				const TSharedPtr<FJsonObject> ComponentTags = ItemNamed(ComponentList, TEXT("ComponentTags"));
				Test->TestEqual(
					TEXT("P2Pie StaticMeshComponent ComponentTags is an array"),
					ItemString(ComponentTags, TEXT("typeKind")),
					FString(TEXT("array")));

				bComponentDiscoveryVerified = Mobility.IsValid() && OverrideMaterials.IsValid()
					&& StaticMesh.IsValid() && CastShadow.IsValid();
				ComponentEvidence = ComponentList;
			}
			else
			{
				Test->AddError(FString::Printf(
					TEXT("P2Pie StaticMeshComponent property discovery failed: %s %s"),
					*Code,
					*Message));
			}

			// ---- scalar reads --------------------------------------------------
			bool bScalarVerified = false;
			TSharedPtr<FJsonObject> ScalarEvidence;
			{
				FObjectPropertyReadRequest Read;
				Read.Target.Actor.PieInstanceId = State->ServerInstanceId;
				Read.Target.Actor.ActorName = State->FixtureActorName;
				Read.PropertyNames = {
					TEXT("Health"),
					TEXT("Ammo"),
					TEXT("bIsDead"),
					TEXT("StateName"),
					TEXT("Description"),
					TEXT("Mode") };

				TSharedPtr<FJsonObject> Result;
				if (Test->TestTrue(
					TEXT("P2Pie scalar read succeeds"),
					TryReadPieObjectProperties(Read, Result, Code, Message)))
				{
					Test->TestTrue(TEXT("P2Pie scalar read is read-only"), Result->GetBoolField(TEXT("readOnly")));
					Test->TestEqual(
						TEXT("P2Pie scalar read reports the exact object path"),
						Result->GetStringField(TEXT("objectPath")),
						State->FixtureActorPath);
					Test->TestEqual(
						TEXT("P2Pie scalar read counts the requested names"),
						static_cast<int32>(Result->GetNumberField(TEXT("requestedCount"))),
						6);

					const TSharedPtr<FJsonObject> Health = ItemNamed(Result, TEXT("Health"));
					Test->TestEqual(
						TEXT("P2Pie Health status"),
						ItemString(Health, TEXT("status")),
						FString(TEXT("ok")));
					Test->TestTrue(TEXT("P2Pie Health value is returned"), ItemBool(Health, TEXT("valueReturned")));
					Test->TestEqual(
						TEXT("P2Pie Health declared type"),
						ItemString(Health, TEXT("type")),
						FString(TEXT("float")));
					Test->TestEqual(TEXT("P2Pie Health value"), ItemNumber(Health, TEXT("value")), 42.5, 0.0001);

					const TSharedPtr<FJsonObject> Ammo = ItemNamed(Result, TEXT("Ammo"));
					Test->TestEqual(TEXT("P2Pie Ammo typeKind"), ItemString(Ammo, TEXT("typeKind")), FString(TEXT("int32")));
					Test->TestEqual(TEXT("P2Pie Ammo value"), ItemNumber(Ammo, TEXT("value")), 9.0, 0.0);

					const TSharedPtr<FJsonObject> Dead = ItemNamed(Result, TEXT("bIsDead"));
					Test->TestEqual(
						TEXT("P2Pie bIsDead typeKind"),
						ItemString(Dead, TEXT("typeKind")),
						FString(TEXT("bool")));
					Test->TestFalse(TEXT("P2Pie bIsDead value"), ItemBool(Dead, TEXT("value")));

					const TSharedPtr<FJsonObject> StateName = ItemNamed(Result, TEXT("StateName"));
					Test->TestEqual(
						TEXT("P2Pie StateName typeKind"),
						ItemString(StateName, TEXT("typeKind")),
						FString(TEXT("name")));
					Test->TestEqual(
						TEXT("P2Pie StateName value"),
						ItemString(StateName, TEXT("value")),
						FString(TEXT("Running")));

					const TSharedPtr<FJsonObject> Description = ItemNamed(Result, TEXT("Description"));
					Test->TestEqual(
						TEXT("P2Pie Description value"),
						ItemString(Description, TEXT("value")),
						FString(TEXT("PieP2 fixture")));
					Test->TestFalse(
						TEXT("P2Pie short string is not truncated"),
						ItemBool(Description, TEXT("truncated")));

					const TSharedPtr<FJsonObject> Mode = ItemNamed(Result, TEXT("Mode"));
					Test->TestEqual(
						TEXT("P2Pie Mode typeKind"),
						ItemString(Mode, TEXT("typeKind")),
						FString(TEXT("enum")));
					Test->TestEqual(
						TEXT("P2Pie Mode entry"),
						ItemString(Mode, TEXT("value")),
						FString(TEXT("Running")));
					Test->TestEqual(
						TEXT("P2Pie Mode enum name"),
						ItemString(Mode, TEXT("enumName")),
						FString(TEXT("EPieP2Mode")));
					Test->TestTrue(TEXT("P2Pie Mode entry is known"), ItemBool(Mode, TEXT("enumEntryKnown")));

					bScalarVerified = Health.IsValid() && Ammo.IsValid() && Dead.IsValid()
						&& StateName.IsValid() && Mode.IsValid();
					ScalarEvidence = Result;
				}
				else
				{
					Test->AddError(FString::Printf(TEXT("P2Pie scalar read failed: %s %s"), *Code, *Message));
				}
			}

			// ---- struct expansion ---------------------------------------------
			bool bStructVerified = false;
			TSharedPtr<FJsonObject> StructEvidence;
			{
				FObjectPropertyReadRequest Read;
				Read.Target.Actor.PieInstanceId = State->ServerInstanceId;
				Read.Target.Actor.ActorPath = State->FixtureActorPath;
				Read.PropertyNames = { TEXT("VectorValue"), TEXT("RotatorValue"), TEXT("TransformValue") };
				TSharedPtr<FJsonObject> Result;
				if (Test->TestTrue(
					TEXT("P2Pie struct read succeeds"),
					TryReadPieObjectProperties(Read, Result, Code, Message)))
				{
					const TSharedPtr<FJsonObject> Vector = ItemNamed(Result, TEXT("VectorValue"));
					Test->TestEqual(
						TEXT("P2Pie VectorValue status"),
						ItemString(Vector, TEXT("status")),
						FString(TEXT("ok")));
					Test->TestEqual(
						TEXT("P2Pie VectorValue struct name"),
						ItemString(Vector, TEXT("structName")),
						FString(TEXT("Vector")));
					Test->TestEqual(
						TEXT("P2Pie VectorValue member count"),
						static_cast<int32>(ItemNumber(Vector, TEXT("memberCount"))),
						3);
					Test->TestEqual(
						TEXT("P2Pie VectorValue X"),
						ItemNumber(MemberNamed(Vector, TEXT("X")), TEXT("value")),
						1.5,
						0.0001);
					Test->TestEqual(
						TEXT("P2Pie VectorValue Y"),
						ItemNumber(MemberNamed(Vector, TEXT("Y")), TEXT("value")),
						-2.25,
						0.0001);
					Test->TestEqual(
						TEXT("P2Pie VectorValue Z"),
						ItemNumber(MemberNamed(Vector, TEXT("Z")), TEXT("value")),
						3.75,
						0.0001);
					Test->TestEqual(
						TEXT("P2Pie VectorValue member type"),
						ItemString(MemberNamed(Vector, TEXT("X")), TEXT("typeKind")),
						FString(TEXT("double")));

					const TSharedPtr<FJsonObject> Rotator = ItemNamed(Result, TEXT("RotatorValue"));
					Test->TestEqual(
						TEXT("P2Pie RotatorValue member count"),
						static_cast<int32>(ItemNumber(Rotator, TEXT("memberCount"))),
						3);
					Test->TestEqual(
						TEXT("P2Pie RotatorValue Pitch"),
						ItemNumber(MemberNamed(Rotator, TEXT("Pitch")), TEXT("value")),
						10.0,
						0.0001);
					Test->TestEqual(
						TEXT("P2Pie RotatorValue Yaw"),
						ItemNumber(MemberNamed(Rotator, TEXT("Yaw")), TEXT("value")),
						20.0,
						0.0001);
					Test->TestEqual(
						TEXT("P2Pie RotatorValue Roll"),
						ItemNumber(MemberNamed(Rotator, TEXT("Roll")), TEXT("value")),
						30.0,
						0.0001);

					const TSharedPtr<FJsonObject> Transform = ItemNamed(Result, TEXT("TransformValue"));
					Test->TestEqual(
						TEXT("P2Pie TransformValue struct name"),
						ItemString(Transform, TEXT("structName")),
						FString(TEXT("Transform")));
					const TSharedPtr<FJsonObject> Translation =
						MemberNamed(Transform, TEXT("Translation"));
					Test->TestEqual(
						TEXT("P2Pie TransformValue Translation X"),
						ItemNumber(MemberNamed(Translation, TEXT("X")), TEXT("value")),
						100.0,
						0.0001);
					Test->TestEqual(
						TEXT("P2Pie TransformValue Translation Y"),
						ItemNumber(MemberNamed(Translation, TEXT("Y")), TEXT("value")),
						200.0,
						0.0001);
					Test->TestEqual(
						TEXT("P2Pie TransformValue Translation Z"),
						ItemNumber(MemberNamed(Translation, TEXT("Z")), TEXT("value")),
						300.0,
						0.0001);
					const TSharedPtr<FJsonObject> Rotation = MemberNamed(Transform, TEXT("Rotation"));
					Test->TestEqual(
						TEXT("P2Pie TransformValue nested Rotation member count"),
						static_cast<int32>(ItemNumber(Rotation, TEXT("memberCount"))),
						4);
					Test->TestEqual(
						TEXT("P2Pie TransformValue Rotation W"),
						ItemNumber(MemberNamed(Rotation, TEXT("W")), TEXT("value")),
						0.70710678118654752,
						0.0001);

					bStructVerified = Vector.IsValid() && Rotator.IsValid()
						&& Translation.IsValid() && Rotation.IsValid();
					StructEvidence = Result;
				}
				else
				{
					Test->AddError(FString::Printf(TEXT("P2Pie struct read failed: %s %s"), *Code, *Message));
				}
			}

			// ---- container paging ---------------------------------------------
			bool bPagingVerified = false;
			TSharedPtr<FJsonObject> PagingEvidence;
			{
				auto ReadArrayPage = [&](const int32 PageOffset, const int32 PageLimit, TSharedPtr<FJsonObject>& OutResult)
				{
					FObjectPropertyReadRequest Read;
					Read.Target.Actor.PieInstanceId = State->ServerInstanceId;
					Read.Target.Actor.ActorPath = State->FixtureActorPath;
					Read.PropertyNames = { TEXT("IntArray") };
					Read.Offset = PageOffset;
					Read.Limit = PageLimit;
					return TryReadPieObjectProperties(Read, OutResult, Code, Message);
				};

				TSharedPtr<FJsonObject> Page1;
				TSharedPtr<FJsonObject> Page2;
				TSharedPtr<FJsonObject> Page3;
				const bool bPagesOk = ReadArrayPage(0, 3, Page1)
					&& ReadArrayPage(3, 3, Page2)
					&& ReadArrayPage(6, 3, Page3);
				if (Test->TestTrue(TEXT("P2Pie array paging reads succeed"), bPagesOk))
				{
					const TSharedPtr<FJsonObject> First = ItemNamed(Page1, TEXT("IntArray"));
					const TSharedPtr<FJsonObject> Second = ItemNamed(Page2, TEXT("IntArray"));
					const TSharedPtr<FJsonObject> Third = ItemNamed(Page3, TEXT("IntArray"));

					Test->TestEqual(
						TEXT("P2Pie array totalCount is stable on page 1"),
						static_cast<int32>(ItemNumber(First, TEXT("totalCount"))),
						7);
					Test->TestEqual(
						TEXT("P2Pie array totalCount is stable on page 2"),
						static_cast<int32>(ItemNumber(Second, TEXT("totalCount"))),
						7);
					Test->TestEqual(
						TEXT("P2Pie array totalCount is stable on page 3"),
						static_cast<int32>(ItemNumber(Third, TEXT("totalCount"))),
						7);
					Test->TestTrue(TEXT("P2Pie page 1 has more"), ItemBool(First, TEXT("hasMore")));
					Test->TestTrue(TEXT("P2Pie page 2 has more"), ItemBool(Second, TEXT("hasMore")));
					Test->TestFalse(TEXT("P2Pie page 3 is the last page"), ItemBool(Third, TEXT("hasMore")));

					const TArray<TSharedPtr<FJsonValue>>* FirstItems = ItemArray(First, TEXT("items"));
					const TArray<TSharedPtr<FJsonValue>>* SecondItems = ItemArray(Second, TEXT("items"));
					const TArray<TSharedPtr<FJsonValue>>* ThirdItems = ItemArray(Third, TEXT("items"));
					Test->TestEqual(TEXT("P2Pie page 1 size"), FirstItems != nullptr ? FirstItems->Num() : 0, 3);
					Test->TestEqual(TEXT("P2Pie page 2 size"), SecondItems != nullptr ? SecondItems->Num() : 0, 3);
					Test->TestEqual(TEXT("P2Pie page 3 size"), ThirdItems != nullptr ? ThirdItems->Num() : 0, 1);

					auto ItemAt = [](const TArray<TSharedPtr<FJsonValue>>* Items, const int32 Index) -> TSharedPtr<FJsonObject>
					{
						if (Items == nullptr || !Items->IsValidIndex(Index) || !(*Items)[Index].IsValid())
						{
							return nullptr;
						}
						return (*Items)[Index]->AsObject();
					};
					Test->TestEqual(
						TEXT("P2Pie page 1 first value"),
						ItemNumber(ItemAt(FirstItems, 0), TEXT("value")),
						10.0,
						0.0);
					Test->TestEqual(
						TEXT("P2Pie page 1 last index"),
						static_cast<int32>(ItemNumber(ItemAt(FirstItems, 2), TEXT("index"))),
						2);
					Test->TestEqual(
						TEXT("P2Pie page 2 first index"),
						static_cast<int32>(ItemNumber(ItemAt(SecondItems, 0), TEXT("index"))),
						3);
					Test->TestEqual(
						TEXT("P2Pie page 2 first value"),
						ItemNumber(ItemAt(SecondItems, 0), TEXT("value")),
						40.0,
						0.0);
					Test->TestEqual(
						TEXT("P2Pie page 3 first value"),
						ItemNumber(ItemAt(ThirdItems, 0), TEXT("value")),
						70.0,
						0.0);

					// Pages must not overlap: the three index windows are disjoint.
					TSet<int32> SeenIndices;
					bool bOverlap = false;
					for (const TArray<TSharedPtr<FJsonValue>>* Items : { FirstItems, SecondItems, ThirdItems })
					{
						if (Items == nullptr)
						{
							continue;
						}
						for (const TSharedPtr<FJsonValue>& Value : *Items)
						{
							TSharedPtr<FJsonObject> Item;
							if (Value.IsValid())
							{
								Item = Value->AsObject();
							}
							const int32 Index = static_cast<int32>(ItemNumber(Item, TEXT("index")));
							if (SeenIndices.Contains(Index))
							{
								bOverlap = true;
							}
							SeenIndices.Add(Index);
						}
					}
					Test->TestFalse(TEXT("P2Pie array pages do not overlap"), bOverlap);
					Test->TestEqual(TEXT("P2Pie three pages cover the whole array"), SeenIndices.Num(), 7);

					bPagingVerified = First.IsValid() && Second.IsValid() && Third.IsValid();
					PagingEvidence = Page1;
				}
				else
				{
					Test->AddError(FString::Printf(TEXT("P2Pie array paging failed: %s %s"), *Code, *Message));
				}

				// Set and Map share the same paging contract.
				FObjectPropertyReadRequest SetRead;
				SetRead.Target.Actor.PieInstanceId = State->ServerInstanceId;
				SetRead.Target.Actor.ActorPath = State->FixtureActorPath;
				SetRead.PropertyNames = { TEXT("IntSet") };
				SetRead.Limit = 10;
				TSharedPtr<FJsonObject> SetResult;
				if (Test->TestTrue(
					TEXT("P2Pie set read succeeds"),
					TryReadPieObjectProperties(SetRead, SetResult, Code, Message)))
				{
					const TSharedPtr<FJsonObject> SetItem = ItemNamed(SetResult, TEXT("IntSet"));
					Test->TestEqual(
						TEXT("P2Pie set totalCount"),
						static_cast<int32>(ItemNumber(SetItem, TEXT("totalCount"))),
						4);
					Test->TestEqual(
						TEXT("P2Pie set returnedCount"),
						static_cast<int32>(ItemNumber(SetItem, TEXT("returnedCount"))),
						4);
					Test->TestFalse(TEXT("P2Pie set page has no more"), ItemBool(SetItem, TEXT("hasMore")));
					TSet<int32> SetValues;
					const TArray<TSharedPtr<FJsonValue>>* SetItems = ItemArray(SetItem, TEXT("items"));
					if (SetItems != nullptr)
					{
						for (const TSharedPtr<FJsonValue>& Value : *SetItems)
						{
							TSharedPtr<FJsonObject> Item;
							if (Value.IsValid())
							{
								Item = Value->AsObject();
							}
							SetValues.Add(static_cast<int32>(ItemNumber(Item, TEXT("value"))));
						}
					}
					Test->TestEqual(TEXT("P2Pie set contains every element"), SetValues.Num(), 4);
					Test->TestTrue(TEXT("P2Pie set contains 5"), SetValues.Contains(5));
					Test->TestTrue(TEXT("P2Pie set contains 15"), SetValues.Contains(15));
					Test->TestTrue(TEXT("P2Pie set contains 25"), SetValues.Contains(25));
					Test->TestTrue(TEXT("P2Pie set contains 35"), SetValues.Contains(35));
				}

				FObjectPropertyReadRequest MapRead;
				MapRead.Target.Actor.PieInstanceId = State->ServerInstanceId;
				MapRead.Target.Actor.ActorPath = State->FixtureActorPath;
				MapRead.PropertyNames = { TEXT("NameToIntMap") };
				MapRead.Limit = 10;
				TSharedPtr<FJsonObject> MapResult;
				if (Test->TestTrue(
					TEXT("P2Pie map read succeeds"),
					TryReadPieObjectProperties(MapRead, MapResult, Code, Message)))
				{
					const TSharedPtr<FJsonObject> MapItem = ItemNamed(MapResult, TEXT("NameToIntMap"));
					Test->TestEqual(
						TEXT("P2Pie map totalCount"),
						static_cast<int32>(ItemNumber(MapItem, TEXT("totalCount"))),
						3);
					Test->TestFalse(TEXT("P2Pie map page has no more"), ItemBool(MapItem, TEXT("hasMore")));
					Test->TestEqual(
						TEXT("P2Pie map key type"),
						ItemString(MapItem, TEXT("keyTypeKind")),
						FString(TEXT("name")));
					Test->TestEqual(
						TEXT("P2Pie map value type"),
						ItemString(MapItem, TEXT("valueTypeKind")),
						FString(TEXT("int32")));
					bool bAlphaPaired = false;
					const TArray<TSharedPtr<FJsonValue>>* MapItems = ItemArray(MapItem, TEXT("items"));
					if (MapItems != nullptr)
					{
						for (const TSharedPtr<FJsonValue>& Value : *MapItems)
						{
							TSharedPtr<FJsonObject> Item;
							if (Value.IsValid())
							{
								Item = Value->AsObject();
							}
							FString Key;
							if (Item.IsValid() && Item->TryGetStringField(TEXT("key"), Key) && Key == TEXT("Alpha"))
							{
								bAlphaPaired = FMath::IsNearlyEqual(ItemNumber(Item, TEXT("value")), 1.0);
							}
						}
					}
					Test->TestTrue(TEXT("P2Pie map pairs Alpha with 1"), bAlphaPaired);
				}
			}

			// ---- object references --------------------------------------------
			bool bObjectRefVerified = false;
			TSharedPtr<FJsonObject> ObjectRefEvidence;
			{
				FObjectPropertyReadRequest Read;
				Read.Target.Actor.PieInstanceId = State->ServerInstanceId;
				Read.Target.Actor.ActorPath = State->FixtureActorPath;
				Read.PropertyNames = { TEXT("ObjectRefValue"), TEXT("MeshRefValue") };
				TSharedPtr<FJsonObject> Result;
				if (Test->TestTrue(
					TEXT("P2Pie object reference read succeeds"),
					TryReadPieObjectProperties(Read, Result, Code, Message)))
				{
					const TSharedPtr<FJsonObject> ObjectRefItem = ItemNamed(Result, TEXT("ObjectRefValue"));
					const TSharedPtr<FJsonObject>* ObjectRefField = nullptr;
					TSharedPtr<FJsonObject> ObjectRef;
					if (ObjectRefItem.IsValid()
						&& ObjectRefItem->TryGetObjectField(TEXT("objectRef"), ObjectRefField)
						&& ObjectRefField != nullptr
						&& ObjectRefField->IsValid())
					{
						ObjectRef = *ObjectRefField;
					}
					Test->TestTrue(
						TEXT("P2Pie object reference identity is returned"),
						ObjectRef.IsValid());
					if (ObjectRef.IsValid())
					{
						Test->TestEqual(
							TEXT("P2Pie object reference path"),
							ItemString(ObjectRef, TEXT("path")),
							State->MeshComponentPath);
						Test->TestTrue(
							TEXT("P2Pie object reference class is the referenced class"),
							ItemString(ObjectRef, TEXT("path")) != State->FixtureActorPath);
						Test->TestEqual(
							TEXT("P2Pie object reference world key"),
							ItemString(ObjectRef, TEXT("worldKey")),
							State->ServerContextId);
						Test->TestEqual(
							TEXT("P2Pie object reference instance"),
							static_cast<int32>(ItemNumber(ObjectRef, TEXT("pieInstanceId"))),
							State->ServerInstanceId);
						Test->TestFalse(TEXT("P2Pie object reference is not null"), ItemBool(ObjectRef, TEXT("isNull")));
						Test->TestTrue(
							TEXT("P2Pie object reference class names the referenced class"),
							ItemString(ObjectRef, TEXT("class")).Contains(TEXT("StaticMeshComponent")));
					}
					// No recursion: the referenced object's own properties are absent.
					Test->TestFalse(
						TEXT("P2Pie object reference does not expand members"),
						ItemHasField(ObjectRefItem, TEXT("members")));
					Test->TestFalse(
						TEXT("P2Pie object reference does not expand properties"),
						ItemHasField(ObjectRefItem, TEXT("properties")));
					Test->TestFalse(
						TEXT("P2Pie object reference does not leak the referenced properties"),
						ItemNamed(Result, TEXT("CastShadow")).IsValid());

					bObjectRefVerified = ObjectRef.IsValid();
					ObjectRefEvidence = Result;
				}

				// A null reference must be reported as null identity, never faked.
				FObjectPropertyReadRequest NullRead;
				NullRead.Target.Actor.PieInstanceId = State->ServerInstanceId;
				NullRead.Target.Actor.ActorName = State->NullRefActorName;
				NullRead.PropertyNames = { TEXT("ObjectRefValue") };
				TSharedPtr<FJsonObject> NullResult;
				if (Test->TestTrue(
					TEXT("P2Pie null reference read succeeds"),
					TryReadPieObjectProperties(NullRead, NullResult, Code, Message)))
				{
					const TSharedPtr<FJsonObject> NullItem = ItemNamed(NullResult, TEXT("ObjectRefValue"));
					const TSharedPtr<FJsonObject>* NullObjectRefField = nullptr;
					TSharedPtr<FJsonObject> NullObjectRef;
					if (NullItem.IsValid()
						&& NullItem->TryGetObjectField(TEXT("objectRef"), NullObjectRefField)
						&& NullObjectRefField != nullptr
						&& NullObjectRefField->IsValid())
					{
						NullObjectRef = *NullObjectRefField;
					}
					Test->TestTrue(TEXT("P2Pie null reference reports isNull"), ItemBool(NullObjectRef, TEXT("isNull")));
					Test->TestTrue(
						TEXT("P2Pie null reference reports an empty path"),
						ItemString(NullObjectRef, TEXT("path")).IsEmpty());
					Test->TestFalse(
						TEXT("P2Pie null reference is not in a PIE world"),
						ItemBool(NullObjectRef, TEXT("inPieWorld")));
				}
			}

			// ---- explicit refusals and reflection-only reads -------------------
			bool bUnsupportedVerified = false;
			bool bReflectionOnlyReadVerified = false;
			TSharedPtr<FJsonObject> UnsupportedEvidence;
			{
				FObjectPropertyReadRequest Read;
				Read.Target.Actor.PieInstanceId = State->ServerInstanceId;
				Read.Target.Actor.ActorPath = State->FixtureActorPath;
				Read.PropertyNames = {
					TEXT("TextValue"),
					TEXT("DelegateValue"),
					TEXT("MulticastValue"),
					TEXT("bBitfieldFlag"),
					TEXT("NoSuchProperty"),
					TEXT("NotExposed"),
					TEXT("PrivateEditable") };
				TSharedPtr<FJsonObject> Result;
				if (Test->TestTrue(
					TEXT("P2Pie refusal read succeeds"),
					TryReadPieObjectProperties(Read, Result, Code, Message)))
				{
					auto ExpectRefusal = [&](const TCHAR* Name, const TCHAR* Reason)
					{
						const TSharedPtr<FJsonObject> Item = ItemNamed(Result, Name);
						Test->TestEqual(
							*FString::Printf(TEXT("P2Pie %s status"), Name),
							ItemString(Item, TEXT("status")),
							FString(TEXT("unsupported-type")));
						Test->TestEqual(
							*FString::Printf(TEXT("P2Pie %s refusal reason"), Name),
							ItemString(Item, TEXT("reason")),
							FString(Reason));
						Test->TestFalse(
							*FString::Printf(TEXT("P2Pie %s returns no value"), Name),
							ItemBool(Item, TEXT("valueReturned")));
						Test->TestFalse(
							*FString::Printf(TEXT("P2Pie %s does not fabricate a value field"), Name),
							ItemHasField(Item, TEXT("value")));
					};
					ExpectRefusal(TEXT("TextValue"), TEXT("text-not-readable"));
					ExpectRefusal(TEXT("DelegateValue"), TEXT("delegate-not-readable"));
					ExpectRefusal(TEXT("MulticastValue"), TEXT("delegate-not-readable"));
					ExpectRefusal(TEXT("bBitfieldFlag"), TEXT("bitfield-not-readable"));


					const TSharedPtr<FJsonObject> Missing = ItemNamed(Result, TEXT("NoSuchProperty"));
					Test->TestEqual(
						TEXT("P2Pie unknown property status"),
						ItemString(Missing, TEXT("status")),
						FString(TEXT("unknown")));
					Test->TestEqual(
						TEXT("P2Pie unknown property reason"),
						ItemString(Missing, TEXT("reason")),
						FString(TEXT("property-not-found")));
					Test->TestFalse(
						TEXT("P2Pie unknown property returns no value"),
						ItemBool(Missing, TEXT("valueReturned")));

					// Reflection driven: neither Edit-visible nor native-private
					// gating applies to P2.
					const TSharedPtr<FJsonObject> NotExposed = ItemNamed(Result, TEXT("NotExposed"));
					Test->TestEqual(
						TEXT("P2Pie unexposed property status is ok (no Edit gate)"),
						ItemString(NotExposed, TEXT("status")),
						FString(TEXT("ok")));
					Test->TestEqual(
						TEXT("P2Pie unexposed property value"),
						ItemNumber(NotExposed, TEXT("value")),
						3.0,
						0.0);
					const TSharedPtr<FJsonObject> PrivateEditable = ItemNamed(Result, TEXT("PrivateEditable"));
					Test->TestEqual(
						TEXT("P2Pie native-private property status is ok (reflection driven)"),
						ItemString(PrivateEditable, TEXT("status")),
						FString(TEXT("ok")));
					Test->TestEqual(
						TEXT("P2Pie native-private property value"),
						ItemNumber(PrivateEditable, TEXT("value")),
						11.0,
						0.0);

					bUnsupportedVerified = ItemNamed(Result, TEXT("TextValue")).IsValid()
						&& ItemNamed(Result, TEXT("bBitfieldFlag")).IsValid()
						&& ItemNamed(Result, TEXT("NoSuchProperty")).IsValid();
					bReflectionOnlyReadVerified = ItemNamed(Result, TEXT("NotExposed")).IsValid()
						&& ItemNamed(Result, TEXT("PrivateEditable")).IsValid();
					UnsupportedEvidence = Result;
				}
				else
				{
					Test->AddError(FString::Printf(TEXT("P2Pie refusal read failed: %s %s"), *Code, *Message));
				}
			}

			// ---- world isolation ----------------------------------------------
			bool bIsolationVerified = false;
			{
				// Same object path, wrong world: the P1 resolver must refuse with
				// the same code family the Actor list already uses.
				FObjectPropertyReadRequest CrossWorld;
				CrossWorld.Target.Actor.PieInstanceId = State->ClientInstanceId;
				CrossWorld.Target.Actor.ActorPath = State->FixtureActorPath;
				CrossWorld.PropertyNames = { TEXT("Health") };
				TSharedPtr<FJsonObject> CrossWorldResult;
				const bool bCrossWorldOk = TryReadPieObjectProperties(
					CrossWorld,
					CrossWorldResult,
					Code,
					Message);
				Test->TestFalse(TEXT("P2Pie cross-world property read is rejected"), bCrossWorldOk);
				Test->TestEqual(
					TEXT("P2Pie cross-world property read returns pie-actor-not-found"),
					Code,
					FString(FailureCode(EFailure::ActorNotFound)));
				Test->TestFalse(
					TEXT("P2Pie cross-world property read returns no result"),
					CrossWorldResult.IsValid());
				Test->TestTrue(
					TEXT("P2Pie cross-world error names the target world"),
					Message.Contains(TEXT("client"), ESearchCase::IgnoreCase)
						|| Message.Contains(TEXT("Client"), ESearchCase::CaseSensitive));

				FObjectPropertyListRequest CrossWorldList;
				CrossWorldList.Target.Actor.PieInstanceId = State->ClientInstanceId;
				CrossWorldList.Target.Actor.ActorName = State->FixtureActorName;
				TSharedPtr<FJsonObject> CrossWorldListResult;
				const bool bCrossWorldListOk = TryListPieObjectProperties(
					CrossWorldList,
					CrossWorldListResult,
					Code,
					Message);
				Test->TestFalse(TEXT("P2Pie cross-world property discovery is rejected"), bCrossWorldListOk);
				Test->TestEqual(
					TEXT("P2Pie cross-world discovery returns pie-actor-not-found"),
					Code,
					FString(FailureCode(EFailure::ActorNotFound)));

				// The Server world is unaffected by the refused Client read.
				FObjectPropertyReadRequest ServerStillWorks;
				ServerStillWorks.Target.Actor.PieInstanceId = State->ServerInstanceId;
				ServerStillWorks.Target.Actor.ActorPath = State->FixtureActorPath;
				ServerStillWorks.PropertyNames = { TEXT("Health") };
				TSharedPtr<FJsonObject> ServerStillWorksResult;
				Test->TestTrue(
					TEXT("P2Pie Server read still works after a refused Client read"),
					TryReadPieObjectProperties(ServerStillWorks, ServerStillWorksResult, Code, Message));

				bIsolationVerified = !bCrossWorldOk && !bCrossWorldListOk;
			}

			// ---- read-only proof ----------------------------------------------
			Test->TestEqual(TEXT("P2Pie fixture Health is unchanged"), Fixture->Health, 42.5f);
			Test->TestEqual(
				TEXT("P2Pie fixture IntArray is unchanged"),
				Fixture->IntArray.Num(),
				7);
			Test->TestTrue(
				TEXT("P2Pie fixture object reference is unchanged"),
				Fixture->ObjectRefValue.Get() == Fixture->Mesh.Get());
			Test->TestEqual(
				TEXT("P2Pie fixture Ammo is unchanged"),
				Fixture->Ammo,
				9);

			// ---- output bound --------------------------------------------------
			bool bOutputBounded = true;
			if (ScalarEvidence.IsValid())
			{
				FString Serialized;
				const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Serialized);
				FJsonSerializer::Serialize(ScalarEvidence.ToSharedRef(), Writer);
				bOutputBounded = Serialized.Len() <= 64 * 1024;
			}
			Test->TestTrue(TEXT("P2Pie scalar response stays within 64 KiB"), bOutputBounded);

			// ---- evidence ------------------------------------------------------
			TSharedRef<FJsonObject> Extra = MakeShared<FJsonObject>();
			Extra->SetBoolField(TEXT("discoveryVerified"), bDiscoveryVerified);
			Extra->SetBoolField(TEXT("noVisibilityGateVerified"), bNoVisibilityGateVerified);
			Extra->SetBoolField(TEXT("containerFlagsVerified"), bContainerFlagsVerified);
			Extra->SetBoolField(TEXT("componentDiscoveryVerified"), bComponentDiscoveryVerified);
			Extra->SetBoolField(TEXT("scalarVerified"), bScalarVerified);
			Extra->SetBoolField(TEXT("structVerified"), bStructVerified);
			Extra->SetBoolField(TEXT("pagingVerified"), bPagingVerified);
			Extra->SetBoolField(TEXT("objectRefVerified"), bObjectRefVerified);
			Extra->SetBoolField(TEXT("unsupportedVerified"), bUnsupportedVerified);
			Extra->SetBoolField(TEXT("reflectionOnlyReadVerified"), bReflectionOnlyReadVerified);
			Extra->SetBoolField(TEXT("worldIsolationVerified"), bIsolationVerified);
			Extra->SetBoolField(TEXT("outputBounded"), bOutputBounded);
			if (DiscoveryEvidence.IsValid())
			{
				Extra->SetObjectField(TEXT("actorDiscovery"), DiscoveryEvidence.ToSharedRef());
			}
			if (ComponentEvidence.IsValid())
			{
				Extra->SetObjectField(TEXT("componentDiscovery"), ComponentEvidence.ToSharedRef());
			}
			if (ScalarEvidence.IsValid())
			{
				Extra->SetObjectField(TEXT("scalars"), ScalarEvidence.ToSharedRef());
			}
			if (StructEvidence.IsValid())
			{
				Extra->SetObjectField(TEXT("structs"), StructEvidence.ToSharedRef());
			}
			if (PagingEvidence.IsValid())
			{
				Extra->SetObjectField(TEXT("containerPage"), PagingEvidence.ToSharedRef());
			}
			if (ObjectRefEvidence.IsValid())
			{
				Extra->SetObjectField(TEXT("objectReferences"), ObjectRefEvidence.ToSharedRef());
			}
			if (UnsupportedEvidence.IsValid())
			{
				Extra->SetObjectField(TEXT("refusals"), UnsupportedEvidence.ToSharedRef());
			}
			WriteEvidence(State, Extra);

			RestorePlaySettings(State);
		}

		TSharedRef<FState> State;
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPiePropertyContractTest,
	"UEAgentKit.P0Pie.PropertyContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPiePropertyContractTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPieRuntime;

	ResetPieInstanceHistoryForTests();

	UClass* FixtureClass = APieP2PropertyFixtureActor::StaticClass();
	if (!TestNotNull(TEXT("P2Pie fixture class is registered"), FixtureClass))
	{
		return false;
	}

	auto PropertyOf = [FixtureClass](const TCHAR* Name) -> const FProperty*
	{
		return FixtureClass->FindPropertyByName(FName(Name));
	};
	auto KindOf = [&PropertyOf](const TCHAR* Name) -> int32
	{
		const FProperty* Property = PropertyOf(Name);
		return Property != nullptr ? static_cast<int32>(ClassifyProperty(Property)) : -1;
	};
	auto SupportOf = [&PropertyOf](const TCHAR* Name) -> FPropertySupport
	{
		return EvaluatePropertySupport(PropertyOf(Name));
	};

	// ---- reflection classification -------------------------------------------
	TestEqual(TEXT("P2Pie Health classifies as float"), KindOf(TEXT("Health")), static_cast<int32>(EPropertyKind::Float));
	TestEqual(TEXT("P2Pie Ammo classifies as int32"), KindOf(TEXT("Ammo")), static_cast<int32>(EPropertyKind::Int32));
	TestEqual(TEXT("P2Pie bIsDead classifies as bool"), KindOf(TEXT("bIsDead")), static_cast<int32>(EPropertyKind::Bool));
	TestEqual(TEXT("P2Pie StateName classifies as name"), KindOf(TEXT("StateName")), static_cast<int32>(EPropertyKind::Name));
	TestEqual(TEXT("P2Pie Description classifies as string"), KindOf(TEXT("Description")), static_cast<int32>(EPropertyKind::String));
	TestEqual(TEXT("P2Pie Mode classifies as enum"), KindOf(TEXT("Mode")), static_cast<int32>(EPropertyKind::Enum));
	TestEqual(TEXT("P2Pie VectorValue classifies as struct"), KindOf(TEXT("VectorValue")), static_cast<int32>(EPropertyKind::Struct));
	TestEqual(TEXT("P2Pie IntArray classifies as array"), KindOf(TEXT("IntArray")), static_cast<int32>(EPropertyKind::Array));
	TestEqual(TEXT("P2Pie NameToIntMap classifies as map"), KindOf(TEXT("NameToIntMap")), static_cast<int32>(EPropertyKind::Map));
	TestEqual(TEXT("P2Pie IntSet classifies as set"), KindOf(TEXT("IntSet")), static_cast<int32>(EPropertyKind::Set));
	TestEqual(TEXT("P2Pie ObjectRefValue classifies as object"), KindOf(TEXT("ObjectRefValue")), static_cast<int32>(EPropertyKind::Object));
	TestEqual(TEXT("P2Pie TextValue classifies as text"), KindOf(TEXT("TextValue")), static_cast<int32>(EPropertyKind::Text));
	TestEqual(TEXT("P2Pie DelegateValue classifies as delegate"), KindOf(TEXT("DelegateValue")), static_cast<int32>(EPropertyKind::Delegate));
	TestEqual(
		TEXT("P2Pie MulticastValue classifies as a multicast delegate"),
		KindOf(TEXT("MulticastValue")),
		static_cast<int32>(EPropertyKind::MulticastDelegate));
	TestEqual(TEXT("P2Pie bBitfieldFlag classifies as a bitfield"), KindOf(TEXT("bBitfieldFlag")), static_cast<int32>(EPropertyKind::Bitfield));
	TestEqual(TEXT("P2Pie Unknown property classifies as unknown"), static_cast<int32>(ClassifyProperty(nullptr)), static_cast<int32>(EPropertyKind::Unknown));

	// ---- stable wire names ----------------------------------------------------
	TestEqual(TEXT("P2Pie float wire name"), FString(PropertyKindName(EPropertyKind::Float)), FString(TEXT("float")));
	TestEqual(TEXT("P2Pie struct wire name"), FString(PropertyKindName(EPropertyKind::Struct)), FString(TEXT("struct")));
	TestEqual(TEXT("P2Pie map wire name"), FString(PropertyKindName(EPropertyKind::Map)), FString(TEXT("map")));
	TestEqual(TEXT("P2Pie object wire name"), FString(PropertyKindName(EPropertyKind::Object)), FString(TEXT("object")));
	TestEqual(TEXT("P2Pie bitfield wire name"), FString(PropertyKindName(EPropertyKind::Bitfield)), FString(TEXT("bitfield")));
	TestEqual(TEXT("P2Pie unknown wire name"), FString(PropertyKindName(EPropertyKind::Unknown)), FString(TEXT("unknown")));

	// ---- per-property status codes -------------------------------------------
	const FString OkCode(PropertyValueStatusCode(EPropertyValueStatus::Ok));
	const FString UnsupportedCode(PropertyValueStatusCode(EPropertyValueStatus::UnsupportedType));
	const FString UnknownCode(PropertyValueStatusCode(EPropertyValueStatus::Unknown));
	const FString NotExposedCode(PropertyValueStatusCode(EPropertyValueStatus::NotExposed));
	TestEqual(TEXT("P2Pie ok status code"), OkCode, FString(TEXT("ok")));
	TestEqual(TEXT("P2Pie unsupported-type status code"), UnsupportedCode, FString(TEXT("unsupported-type")));
	TestEqual(TEXT("P2Pie unknown status code"), UnknownCode, FString(TEXT("unknown")));
	TestEqual(TEXT("P2Pie not-exposed status code"), NotExposedCode, FString(TEXT("not-exposed")));
	TestTrue(
		TEXT("P2Pie status codes are pairwise distinct"),
		OkCode != UnsupportedCode && UnsupportedCode != UnknownCode && UnknownCode != NotExposedCode
			&& OkCode != UnknownCode && OkCode != NotExposedCode && UnsupportedCode != NotExposedCode);

	// ---- structural support verdicts -----------------------------------------
	TestTrue(TEXT("P2Pie float property is supported"), SupportOf(TEXT("Health")).bSupported);
	TestTrue(TEXT("P2Pie enum property is supported"), SupportOf(TEXT("Mode")).bSupported);
	TestTrue(TEXT("P2Pie Vector struct is supported"), SupportOf(TEXT("VectorValue")).bSupported);
	TestTrue(TEXT("P2Pie Transform struct is supported"), SupportOf(TEXT("TransformValue")).bSupported);
	TestTrue(TEXT("P2Pie array of int32 is supported"), SupportOf(TEXT("IntArray")).bSupported);
	TestTrue(TEXT("P2Pie map of name to int32 is supported"), SupportOf(TEXT("NameToIntMap")).bSupported);
	TestTrue(TEXT("P2Pie set of int32 is supported"), SupportOf(TEXT("IntSet")).bSupported);
	TestTrue(TEXT("P2Pie object reference is supported"), SupportOf(TEXT("ObjectRefValue")).bSupported);
	TestTrue(
		TEXT("P2Pie reflected-but-invisible int32 property is supported"),
		SupportOf(TEXT("NotExposed")).bSupported);
	TestTrue(
		TEXT("P2Pie native-private int32 property is supported"),
		SupportOf(TEXT("PrivateEditable")).bSupported);

	TestFalse(TEXT("P2Pie FText is not supported"), SupportOf(TEXT("TextValue")).bSupported);
	TestEqual(
		TEXT("P2Pie FText refusal reason"),
		SupportOf(TEXT("TextValue")).Reason,
		FString(TEXT("text-not-readable")));
	TestEqual(
		TEXT("P2Pie delegate refusal reason"),
		SupportOf(TEXT("DelegateValue")).Reason,
		FString(TEXT("delegate-not-readable")));
	TestEqual(
		TEXT("P2Pie multicast delegate refusal reason"),
		SupportOf(TEXT("MulticastValue")).Reason,
		FString(TEXT("delegate-not-readable")));
	TestEqual(
		TEXT("P2Pie bitfield refusal reason"),
		SupportOf(TEXT("bBitfieldFlag")).Reason,
		FString(TEXT("bitfield-not-readable")));
	TestEqual(
		TEXT("P2Pie missing property refusal reason"),
		EvaluatePropertySupport(nullptr).Reason,
		FString(TEXT("property-not-found")));

	// ---- descriptors ---------------------------------------------------------
	{
		const TSharedRef<FJsonObject> Descriptor = DescribePropertyDescriptor(PropertyOf(TEXT("IntArray")));
		TestEqual(
			TEXT("P2Pie IntArray descriptor name"),
			Descriptor->GetStringField(TEXT("name")),
			FString(TEXT("IntArray")));
		TestTrue(TEXT("P2Pie IntArray descriptor isArray"), Descriptor->GetBoolField(TEXT("isArray")));
		TestFalse(TEXT("P2Pie IntArray descriptor isMap"), Descriptor->GetBoolField(TEXT("isMap")));
		TestTrue(TEXT("P2Pie IntArray descriptor supported"), Descriptor->GetBoolField(TEXT("supported")));
		TestFalse(
			TEXT("P2Pie supported descriptor has no refusal reason"),
			Descriptor->HasField(TEXT("unsupportedReason")));

		const TSharedRef<FJsonObject> VectorDescriptor = DescribePropertyDescriptor(PropertyOf(TEXT("VectorValue")));
		TestTrue(TEXT("P2Pie Vector descriptor isStruct"), VectorDescriptor->GetBoolField(TEXT("isStruct")));
		TestEqual(
			TEXT("P2Pie Vector descriptor declared type"),
			VectorDescriptor->GetStringField(TEXT("type")),
			FString(TEXT("FVector")));
		TestEqual(
			TEXT("P2Pie Vector descriptor declaring class"),
			VectorDescriptor->GetStringField(TEXT("declaringClassPath")),
			FixtureClass->GetPathName());

		const TSharedRef<FJsonObject> ObjectRefDescriptor =
			DescribePropertyDescriptor(PropertyOf(TEXT("ObjectRefValue")));
		TestTrue(
			TEXT("P2Pie ObjectRef descriptor isObjectRef"),
			ObjectRefDescriptor->GetBoolField(TEXT("isObjectRef")));

		const TSharedRef<FJsonObject> BitfieldDescriptor =
			DescribePropertyDescriptor(PropertyOf(TEXT("bBitfieldFlag")));
		TestTrue(TEXT("P2Pie bitfield descriptor isBitfield"), BitfieldDescriptor->GetBoolField(TEXT("isBitfield")));
		TestFalse(TEXT("P2Pie bitfield descriptor supported"), BitfieldDescriptor->GetBoolField(TEXT("supported")));
		TestEqual(
			TEXT("P2Pie bitfield descriptor reason"),
			BitfieldDescriptor->GetStringField(TEXT("unsupportedReason")),
			FString(TEXT("bitfield-not-readable")));

		const TSharedRef<FJsonObject> MissingDescriptor = DescribePropertyDescriptor(nullptr);
		TestFalse(TEXT("P2Pie missing descriptor supported"), MissingDescriptor->GetBoolField(TEXT("supported")));
		TestEqual(
			TEXT("P2Pie missing descriptor reason"),
			MissingDescriptor->GetStringField(TEXT("unsupportedReason")),
			FString(TEXT("property-not-found")));
	}

	// ---- request validation (PIE is stopped here) ----------------------------
	TSharedPtr<FJsonObject> Result;
	FString Code;
	FString Message;
	const FString InvalidCode(FailureCode(EFailure::InvalidRequest));
	const FString NotRunningCode(FailureCode(EFailure::InstanceNotRunning));

	auto BaseRead = []()
	{
		FObjectPropertyReadRequest Request;
		Request.Target.Actor.PieInstanceId = 0;
		Request.Target.Actor.ActorName = TEXT("P2PieContractActor");
		return Request;
	};

	{
		FObjectPropertyReadRequest Empty = BaseRead();
		TestFalse(
			TEXT("P2Pie empty propertyNames is rejected"),
			TryReadPieObjectProperties(Empty, Result, Code, Message));
		TestEqual(TEXT("P2Pie empty propertyNames returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest TooMany = BaseRead();
		for (int32 Index = 0; Index < MaxReadObjectProperties + 1; ++Index)
		{
			TooMany.PropertyNames.Add(FString::Printf(TEXT("P2PieProp%d"), Index));
		}
		TestFalse(
			TEXT("P2Pie more than the read cap of distinct names is rejected"),
			TryReadPieObjectProperties(TooMany, Result, Code, Message));
		TestEqual(TEXT("P2Pie oversized propertyNames returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		// Duplicates are collapsed before the cap is applied, so 18 requested
		// names that collapse to 16 distinct ones are accepted.
		FObjectPropertyReadRequest Deduplicated = BaseRead();
		for (int32 Index = 0; Index < MaxReadObjectProperties; ++Index)
		{
			Deduplicated.PropertyNames.Add(FString::Printf(TEXT("P2PieProp%d"), Index));
		}
		Deduplicated.PropertyNames.Add(TEXT("P2PieProp0"));
		Deduplicated.PropertyNames.Add(TEXT("P2PieProp0"));
		TestFalse(
			TEXT("P2Pie deduplicated names still reach world resolution"),
			TryReadPieObjectProperties(Deduplicated, Result, Code, Message));
		TestEqual(
			TEXT("P2Pie deduplicated names pass validation and fail on stopped PIE"),
			Code,
			NotRunningCode);
	}
	{
		FObjectPropertyReadRequest Dotted = BaseRead();
		Dotted.PropertyNames = { TEXT("VectorValue.X") };
		TestFalse(
			TEXT("P2Pie dotted property path is rejected"),
			TryReadPieObjectProperties(Dotted, Result, Code, Message));
		TestEqual(TEXT("P2Pie dotted path returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest LongName = BaseRead();
		FString OversizedName;
		OversizedName.AppendChar(TEXT('A'));
		for (int32 Index = 0; Index < MaxReadPropertyNameChars; ++Index)
		{
			OversizedName.AppendChar(TEXT('b'));
		}
		LongName.PropertyNames = { OversizedName };
		TestFalse(
			TEXT("P2Pie over-long property name is rejected"),
			TryReadPieObjectProperties(LongName, Result, Code, Message));
		TestEqual(TEXT("P2Pie over-long name returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest NegativeOffset = BaseRead();
		NegativeOffset.PropertyNames = { TEXT("Health") };
		NegativeOffset.Offset = -1;
		TestFalse(
			TEXT("P2Pie negative offset is rejected"),
			TryReadPieObjectProperties(NegativeOffset, Result, Code, Message));
		TestEqual(TEXT("P2Pie negative offset returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest ZeroLimit = BaseRead();
		ZeroLimit.PropertyNames = { TEXT("Health") };
		ZeroLimit.Limit = 0;
		TestFalse(
			TEXT("P2Pie zero limit is rejected"),
			TryReadPieObjectProperties(ZeroLimit, Result, Code, Message));
		TestEqual(TEXT("P2Pie zero limit returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest OversizedLimit = BaseRead();
		OversizedLimit.PropertyNames = { TEXT("Health") };
		OversizedLimit.Limit = MaxContainerPageLimit + 1;
		TestFalse(
			TEXT("P2Pie oversized container limit is rejected"),
			TryReadPieObjectProperties(OversizedLimit, Result, Code, Message));
		TestEqual(TEXT("P2Pie oversized limit returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest BothComponents = BaseRead();
		BothComponents.PropertyNames = { TEXT("Health") };
		BothComponents.Target.ComponentPath = TEXT("/Game/X.X:PersistentLevel.A.C");
		BothComponents.Target.ComponentName = TEXT("C");
		TestFalse(
			TEXT("P2Pie two Component identities are rejected"),
			TryReadPieObjectProperties(BothComponents, Result, Code, Message));
		TestEqual(TEXT("P2Pie two Component identities returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyListRequest OrphanComponentClass;
		OrphanComponentClass.Target.Actor.PieInstanceId = 0;
		OrphanComponentClass.Target.Actor.ActorName = TEXT("P2PieContractActor");
		OrphanComponentClass.Target.ComponentClassPath = TEXT("/Script/Engine.StaticMeshComponent");
		TestFalse(
			TEXT("P2Pie componentClassPath without an identity is rejected"),
			TryListPieObjectProperties(OrphanComponentClass, Result, Code, Message));
		TestEqual(TEXT("P2Pie orphan componentClassPath returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyListRequest StoppedList;
		StoppedList.Target.Actor.PieInstanceId = 0;
		StoppedList.Target.Actor.ActorName = TEXT("P2PieContractActor");
		TestFalse(
			TEXT("P2Pie property discovery without PIE is rejected"),
			TryListPieObjectProperties(StoppedList, Result, Code, Message));
		TestEqual(TEXT("P2Pie discovery without PIE returns pie-not-running"), Code, NotRunningCode);
	}
	{
		FObjectPropertyListRequest TwoIdentities;
		TwoIdentities.Target.Actor.PieInstanceId = 0;
		TwoIdentities.Target.Actor.ActorName = TEXT("P2PieContractActor");
		TwoIdentities.Target.Actor.ActorLabel = TEXT("P2PieContractActor");
		TestFalse(
			TEXT("P2Pie two Actor identities are rejected"),
			TryListPieObjectProperties(TwoIdentities, Result, Code, Message));
		TestEqual(TEXT("P2Pie two Actor identities returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest StoppedRead = BaseRead();
		StoppedRead.PropertyNames = { TEXT("Health") };
		TestFalse(
			TEXT("P2Pie property read without PIE is rejected"),
			TryReadPieObjectProperties(StoppedRead, Result, Code, Message));
		TestEqual(TEXT("P2Pie read without PIE returns pie-not-running"), Code, NotRunningCode);
		TestFalse(TEXT("P2Pie refused read returns no result object"), Result.IsValid());
	}

	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPiePropertyReadRuntimeTest,
	"UEAgentKit.P0Pie.PropertyReadRuntime",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPiePropertyReadRuntimeTest::RunTest(const FString& Parameters)
{
	TSharedRef<UEAgentKitP2PropertyRuntime::FState> State =
		MakeShared<UEAgentKitP2PropertyRuntime::FState>();
	State->Test = this;

	ULevelEditorPlaySettings* PlaySettings = GetMutableDefault<ULevelEditorPlaySettings>();
	if (!TestNotNull(TEXT("P2Pie PIE play settings are available"), PlaySettings))
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
	ADD_LATENT_AUTOMATION_COMMAND(UEAgentKitP2PropertyRuntime::FWaitForPropertyContexts(State));
	ADD_LATENT_AUTOMATION_COMMAND(UEAgentKitP2PropertyRuntime::FDwellBeforeStopping(1.0));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS

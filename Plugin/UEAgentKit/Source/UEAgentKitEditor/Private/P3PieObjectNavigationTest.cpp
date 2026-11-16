// P3 native evidence for object-reference navigation across PIE objects.
//
// The contract under test: an `objectPath` target may address ANY UObject that
// belongs to exactly one PIE world, and the `objectRef` a read returns can be
// fed straight back in as the next hop's `objectPath`.  Every hop is its own
// call; nothing recurses automatically.
//
//  * `UEAgentKit.P0Pie.ObjectNavigationContract` runs with PIE stopped and
//    proves the target-shape policy, the exclusion rules and the new
//    `pie-object-not-found` wire code.
//  * `UEAgentKit.P0Pie.ObjectNavigationRuntime` starts one disposable
//    listen-server PIE session and walks a real three-hop chain
//    Actor -> Component -> payload UObject, then proves that a reference into a
//    different PIE world is flagged and refused instead of being followed.
//
// Nothing here writes an asset, saves a package or opens the SQLite index.

#include "PieObjectProperties.h"
#include "PieP3NavFixture.h"
#include "PieRuntimeInspection.h"

#include "Components/ActorComponent.h"
#include "Editor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "GameFramework/WorldSettings.h"
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

namespace UEAgentKitP3ObjectNav
{
	const TCHAR* const EvidenceRelativeDirectory = TEXT("PieP3ObjectNavigation");

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

	/** The `objectRef` sub-object of one property item. */
	TSharedPtr<FJsonObject> ObjectRefOf(const TSharedPtr<FJsonObject>& Item)
	{
		const TSharedPtr<FJsonObject>* Field = nullptr;
		if (!Item.IsValid() || !Item->TryGetObjectField(TEXT("objectRef"), Field)
			|| Field == nullptr || !Field->IsValid())
		{
			return nullptr;
		}
		return *Field;
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

		int32 ServerInstanceId = INDEX_NONE;
		int32 ClientInstanceId = INDEX_NONE;
		FString ServerContextId;
		FString ClientContextId;
		FString ServerWorldPath;
		int32 ContextCount = 0;

		FString ActorPath;
		FString ActorName;
		FString ComponentPath;
		FString ComponentClassPath;
		FString PayloadPath;
		FString PayloadClassPath;
		FString ClientObjectPath;

		bool bCompleted = false;
		double StartedAt = 0.0;
		double ReadySince = 0.0;
	};

	void RestorePlaySettings(const TSharedRef<FState>& State)
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

	class FWaitForNavContexts final : public IAutomationLatentCommand
	{
	public:
		explicit FWaitForNavContexts(TSharedRef<FState> InState)
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
			if (!Test->TestNotNull(TEXT("P3Nav server world context is available"), Server))
			{
				return;
			}
			if (!Test->TestNotNull(TEXT("P3Nav client world context is available"), Client))
			{
				return;
			}
			State->ServerInstanceId = Server->PieInstanceId;
			State->ServerContextId = Server->ContextId;
			State->ServerWorldPath = Server->WorldPath;
			State->ClientInstanceId = Client->PieInstanceId;
			State->ClientContextId = Client->ContextId;
			UWorld* ServerWorld = Server->World;
			UWorld* ClientWorld = Client->World;
			if (!Test->TestNotNull(TEXT("P3Nav server world is resolvable"), ServerWorld))
			{
				return;
			}
			if (!Test->TestNotNull(TEXT("P3Nav client world is resolvable"), ClientWorld))
			{
				return;
			}

			// ---- fixture and the real three-hop chain --------------------------
			FActorSpawnParameters SpawnParameters;
			SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
			SpawnParameters.Name = FName(TEXT("PieP3NavFixture"));
			APieP3NavFixtureActor* Fixture = ServerWorld->SpawnActor<APieP3NavFixtureActor>(
				APieP3NavFixtureActor::StaticClass(),
				FVector(300.0f, 0.0f, 100.0f),
				FRotator::ZeroRotator,
				SpawnParameters);
			if (!Test->TestNotNull(TEXT("P3Nav fixture Actor spawns"), Fixture))
			{
				RestorePlaySettings(State);
				return;
			}
			State->ActorPath = Fixture->GetPathName();
			State->ActorName = Fixture->GetName();
			UPieP3NavFixtureComponent* NavComponent = Fixture->NavComponent;
			if (!Test->TestNotNull(TEXT("P3Nav fixture Component exists"), NavComponent))
			{
				RestorePlaySettings(State);
				return;
			}
			State->ComponentPath = NavComponent->GetPathName();
			State->ComponentClassPath = NavComponent->GetClass()->GetPathName();

			// Hop 3's object is created here and owned by the component, so the
			// chain is exactly Actor -> Component -> payload UObject.
			UPieP3PayloadObject* Payload = NewObject<UPieP3PayloadObject>(
				NavComponent,
				TEXT("PieP3Payload"));
			if (!Test->TestNotNull(TEXT("P3Nav payload UObject is created"), Payload))
			{
				RestorePlaySettings(State);
				return;
			}
			Payload->Label = TEXT("hop-three");
			Payload->Count = 33;
			Payload->Ratio = 0.75f;
			NavComponent->Payload = Payload;
			State->PayloadPath = Payload->GetPathName();
			State->PayloadClassPath = Payload->GetClass()->GetPathName();

			FString Code;
			FString Message;
			TSharedPtr<FJsonObject> Hop1;
			TSharedPtr<FJsonObject> Hop2;
			TSharedPtr<FJsonObject> Hop3;
			bool bHop1Verified = false;
			bool bHop2Verified = false;
			bool bHop3Verified = false;
			bool bNoRecursionVerified = false;
			bool bReadOnlyVerified = true;

			// ---- hop 1: Actor -> Component ------------------------------------
			{
				FObjectPropertyReadRequest Read;
				Read.Target.Actor.PieInstanceId = State->ServerInstanceId;
				Read.Target.Actor.ActorPath = State->ActorPath;
				Read.PropertyNames = { TEXT("NavComponent"), TEXT("Health") };
				if (Test->TestTrue(
					TEXT("P3Nav hop 1 (Actor -> Component) succeeds"),
					TryReadPieObjectProperties(Read, Hop1, Code, Message)))
				{
					Test->TestEqual(
						TEXT("P3Nav hop 1 target kind"),
						Hop1->GetStringField(TEXT("targetKind")),
						FString(TEXT("actor")));
					Test->TestEqual(
						TEXT("P3Nav hop 1 object path"),
						Hop1->GetStringField(TEXT("objectPath")),
						State->ActorPath);
					Test->TestEqual(
						TEXT("P3Nav hop 1 class path"),
						Hop1->GetStringField(TEXT("classPath")),
						Fixture->GetClass()->GetPathName());
					Test->TestEqual(
						TEXT("P3Nav hop 1 world key"),
						Hop1->GetStringField(TEXT("worldKey")),
						State->ServerContextId);
					Test->TestEqual(
						TEXT("P3Nav hop 1 instance"),
						static_cast<int32>(Hop1->GetNumberField(TEXT("pieInstanceId"))),
						State->ServerInstanceId);
					bReadOnlyVerified &= Hop1->GetBoolField(TEXT("readOnly"));

					const TSharedPtr<FJsonObject> Health = ItemNamed(Hop1, TEXT("Health"));
					Test->TestEqual(
						TEXT("P3Nav hop 1 scalar unchanged"),
						ItemNumber(Health, TEXT("value")),
						64.0,
						0.0001);

					const TSharedPtr<FJsonObject> NavItem = ItemNamed(Hop1, TEXT("NavComponent"));
					const TSharedPtr<FJsonObject> Ref = ObjectRefOf(NavItem);
					if (Test->TestTrue(TEXT("P3Nav hop 1 returns a component identity"), Ref.IsValid()))
					{
						Test->TestEqual(
							TEXT("P3Nav hop 1 reference path is the Component"),
							ItemString(Ref, TEXT("path")),
							State->ComponentPath);
						Test->TestEqual(
							TEXT("P3Nav hop 1 reference class is the Component class"),
							ItemString(Ref, TEXT("class")),
							State->ComponentClassPath);
						Test->TestEqual(
							TEXT("P3Nav hop 1 reference world key"),
							ItemString(Ref, TEXT("worldKey")),
							State->ServerContextId);
						Test->TestEqual(
							TEXT("P3Nav hop 1 reference instance"),
							static_cast<int32>(ItemNumber(Ref, TEXT("pieInstanceId"))),
							State->ServerInstanceId);
						Test->TestEqual(
							TEXT("P3Nav hop 1 reference outer is the Actor"),
							ItemString(Ref, TEXT("outerPath")),
							State->ActorPath);
						Test->TestFalse(
							TEXT("P3Nav hop 1 reference is not cross-world"),
							ItemBool(Ref, TEXT("crossWorld")));
						Test->TestTrue(
							TEXT("P3Nav hop 1 reference is navigable"),
							ItemBool(Ref, TEXT("propertiesReadable")));
						bHop1Verified = ItemString(Ref, TEXT("path")) == State->ComponentPath;
					}

					// No recursion: the returned identity carries no property payload.
					const TArray<TSharedPtr<FJsonValue>>* Hop1Properties = nullptr;
					Hop1->TryGetArrayField(TEXT("properties"), Hop1Properties);
					Test->TestEqual(
						TEXT("P3Nav hop 1 returns only the requested properties"),
						Hop1Properties != nullptr ? Hop1Properties->Num() : 0,
						2);
					Test->TestFalse(
						TEXT("P3Nav hop 1 reference has no properties array"),
						ItemHasField(NavItem, TEXT("properties")));
					Test->TestFalse(
						TEXT("P3Nav hop 1 reference has no members array"),
						ItemHasField(NavItem, TEXT("members")));
					Test->TestFalse(
						TEXT("P3Nav hop 1 reference does not leak the component properties"),
						ItemNamed(Hop1, TEXT("Gauge")).IsValid());
					Test->TestFalse(
						TEXT("P3Nav hop 1 reference does not leak the component payload"),
						ItemNamed(Hop1, TEXT("Payload")).IsValid());
					bNoRecursionVerified = !ItemHasField(NavItem, TEXT("properties"))
						&& !ItemHasField(NavItem, TEXT("members"))
						&& !ItemNamed(Hop1, TEXT("Payload")).IsValid();
				}
				else
				{
					Test->AddError(FString::Printf(TEXT("P3Nav hop 1 failed: %s %s"), *Code, *Message));
				}
			}

			// ---- hop 2: Component -> payload UObject via objectPath -------------
			{
				const TSharedPtr<FJsonObject> Ref = ObjectRefOf(
					ItemNamed(Hop1, TEXT("NavComponent")));
				const FString NextPath = ItemString(Ref, TEXT("path"));
				if (Test->TestTrue(
					TEXT("P3Nav hop 1 returned a path usable as the next hop"),
					!NextPath.IsEmpty()))
				{
					FObjectPropertyReadRequest Read;
					Read.Target.Actor.PieInstanceId = State->ServerInstanceId;
					Read.Target.ObjectPath = NextPath;
					Read.PropertyNames = { TEXT("Payload"), TEXT("Gauge") };
					if (Test->TestTrue(
						TEXT("P3Nav hop 2 (Component -> payload) succeeds through objectPath"),
						TryReadPieObjectProperties(Read, Hop2, Code, Message)))
					{
						Test->TestEqual(
							TEXT("P3Nav hop 2 target kind is component"),
							Hop2->GetStringField(TEXT("targetKind")),
							FString(TEXT("component")));
						Test->TestEqual(
							TEXT("P3Nav hop 2 object path"),
							Hop2->GetStringField(TEXT("objectPath")),
							State->ComponentPath);
						Test->TestEqual(
							TEXT("P3Nav hop 2 class path"),
							Hop2->GetStringField(TEXT("classPath")),
							State->ComponentClassPath);
						Test->TestEqual(
							TEXT("P3Nav hop 2 world key"),
							Hop2->GetStringField(TEXT("worldKey")),
							State->ServerContextId);
						Test->TestEqual(
							TEXT("P3Nav hop 2 instance"),
							static_cast<int32>(Hop2->GetNumberField(TEXT("pieInstanceId"))),
							State->ServerInstanceId);
						Test->TestEqual(
							TEXT("P3Nav hop 2 outer path is the Actor"),
							Hop2->GetStringField(TEXT("outerPath")),
							State->ActorPath);
						Test->TestEqual(
							TEXT("P3Nav hop 2 outer class is the Actor class"),
							Hop2->GetStringField(TEXT("outerClassPath")),
							Fixture->GetClass()->GetPathName());
						Test->TestEqual(
							TEXT("P3Nav hop 2 actor path is still reported"),
							Hop2->GetStringField(TEXT("actorPath")),
							State->ActorPath);
						bReadOnlyVerified &= Hop2->GetBoolField(TEXT("readOnly"));

						const TSharedPtr<FJsonObject> Gauge = ItemNamed(Hop2, TEXT("Gauge"));
						Test->TestEqual(
							TEXT("P3Nav hop 2 component scalar"),
							ItemNumber(Gauge, TEXT("value")),
							12.5,
							0.0001);

						const TSharedPtr<FJsonObject> Ref2 = ObjectRefOf(ItemNamed(Hop2, TEXT("Payload")));
						if (Test->TestTrue(TEXT("P3Nav hop 2 returns the payload identity"), Ref2.IsValid()))
						{
							Test->TestEqual(
								TEXT("P3Nav hop 2 reference path is the payload"),
								ItemString(Ref2, TEXT("path")),
								State->PayloadPath);
							Test->TestEqual(
								TEXT("P3Nav hop 2 reference class is the payload class"),
								ItemString(Ref2, TEXT("class")),
								State->PayloadClassPath);
							Test->TestEqual(
								TEXT("P3Nav hop 2 reference outer is the Component"),
								ItemString(Ref2, TEXT("outerPath")),
								State->ComponentPath);
							Test->TestEqual(
								TEXT("P3Nav hop 2 reference world key"),
								ItemString(Ref2, TEXT("worldKey")),
								State->ServerContextId);
							Test->TestTrue(
								TEXT("P3Nav hop 2 reference is navigable"),
								ItemBool(Ref2, TEXT("propertiesReadable")));
							Test->TestFalse(
								TEXT("P3Nav hop 2 reference is not cross-world"),
								ItemBool(Ref2, TEXT("crossWorld")));
							bHop2Verified = ItemString(Ref2, TEXT("path")) == State->PayloadPath;
						}
					}
					else
					{
						Test->AddError(FString::Printf(TEXT("P3Nav hop 2 failed: %s %s"), *Code, *Message));
					}
				}
			}

			// ---- hop 3: payload UObject ---------------------------------------
			{
				const TSharedPtr<FJsonObject> Ref2 = ObjectRefOf(ItemNamed(Hop2, TEXT("Payload")));
				const FString NextPath = ItemString(Ref2, TEXT("path"));
				if (Test->TestTrue(
					TEXT("P3Nav hop 2 returned a path usable as the next hop"),
					!NextPath.IsEmpty()))
				{
					FObjectPropertyReadRequest Read;
					Read.Target.Actor.PieInstanceId = State->ServerInstanceId;
					Read.Target.ObjectPath = NextPath;
					Read.PropertyNames = { TEXT("Label"), TEXT("Count"), TEXT("Ratio") };
					if (Test->TestTrue(
						TEXT("P3Nav hop 3 (payload -> scalars) succeeds through objectPath"),
						TryReadPieObjectProperties(Read, Hop3, Code, Message)))
					{
						Test->TestEqual(
							TEXT("P3Nav hop 3 target kind is object"),
							Hop3->GetStringField(TEXT("targetKind")),
							FString(TEXT("object")));
						Test->TestEqual(
							TEXT("P3Nav hop 3 object path"),
							Hop3->GetStringField(TEXT("objectPath")),
							State->PayloadPath);
						Test->TestEqual(
							TEXT("P3Nav hop 3 class path"),
							Hop3->GetStringField(TEXT("classPath")),
							State->PayloadClassPath);
						Test->TestEqual(
							TEXT("P3Nav hop 3 world key"),
							Hop3->GetStringField(TEXT("worldKey")),
							State->ServerContextId);
						Test->TestEqual(
							TEXT("P3Nav hop 3 instance"),
							static_cast<int32>(Hop3->GetNumberField(TEXT("pieInstanceId"))),
							State->ServerInstanceId);
						Test->TestEqual(
							TEXT("P3Nav hop 3 outer path is the Component"),
							Hop3->GetStringField(TEXT("outerPath")),
							State->ComponentPath);
						Test->TestEqual(
							TEXT("P3Nav hop 3 actor path is still reported"),
							Hop3->GetStringField(TEXT("actorPath")),
							State->ActorPath);
						bReadOnlyVerified &= Hop3->GetBoolField(TEXT("readOnly"));

						const TSharedPtr<FJsonObject> Label = ItemNamed(Hop3, TEXT("Label"));
						Test->TestEqual(
							TEXT("P3Nav hop 3 payload Label"),
							ItemString(Label, TEXT("value")),
							FString(TEXT("hop-three")));
						const TSharedPtr<FJsonObject> Count = ItemNamed(Hop3, TEXT("Count"));
						Test->TestEqual(
							TEXT("P3Nav hop 3 payload Count"),
							ItemNumber(Count, TEXT("value")),
							33.0,
							0.0);
						const TSharedPtr<FJsonObject> Ratio = ItemNamed(Hop3, TEXT("Ratio"));
						Test->TestEqual(
							TEXT("P3Nav hop 3 payload Ratio"),
							ItemNumber(Ratio, TEXT("value")),
							0.75,
							0.0001);
						Test->TestEqual(
							TEXT("P3Nav hop 3 payload Count type"),
							ItemString(Count, TEXT("typeKind")),
							FString(TEXT("int32")));
						bHop3Verified = Label.IsValid() && Count.IsValid() && Ratio.IsValid();
					}
					else
					{
						Test->AddError(FString::Printf(TEXT("P3Nav hop 3 failed: %s %s"), *Code, *Message));
					}
				}
			}

			// ---- cross-world refusal ------------------------------------------
			bool bCrossWorldVerified = false;
			TSharedPtr<FJsonObject> CrossWorldEvidence;
			{
				// (a) A path that resolves in the Client world, addressed with the
				// Server instance id, must be refused through the STALE family.
				AWorldSettings* ClientSettings = ClientWorld->GetWorldSettings();
				if (Test->TestNotNull(TEXT("P3Nav client world settings are reachable"), ClientSettings))
				{
					State->ClientObjectPath = ClientSettings->GetPathName();
					FObjectPropertyReadRequest Read;
					Read.Target.Actor.PieInstanceId = State->ServerInstanceId;
					Read.Target.ObjectPath = State->ClientObjectPath;
					Read.PropertyNames = { TEXT("bEnableWorldBoundsChecks") };
					TSharedPtr<FJsonObject> Refused;
					const bool bOk = TryReadPieObjectProperties(Read, Refused, Code, Message);
					Test->TestFalse(
						TEXT("P3Nav client object path is refused in the Server world"),
						bOk);
					Test->TestEqual(
						TEXT("P3Nav cross-world objectPath returns pie-world-stale"),
						Code,
						FString(FailureCode(EFailure::WorldStale)));
					Test->TestFalse(
						TEXT("P3Nav refused cross-world read returns no result"),
						Refused.IsValid());
					Test->TestTrue(
						TEXT("P3Nav cross-world error names the requested world"),
						Message.Contains(TEXT("PIE world"), ESearchCase::IgnoreCase));

					// (b) A reference that points into the other world is flagged
					// instead of being silently treated as local, and cannot be
					// followed from this world.
					NavComponent->Payload = ClientSettings;
					FObjectPropertyReadRequest Flagged;
					Flagged.Target.Actor.PieInstanceId = State->ServerInstanceId;
					Flagged.Target.ObjectPath = State->ComponentPath;
					Flagged.PropertyNames = { TEXT("Payload") };
					TSharedPtr<FJsonObject> FlaggedResult;
					if (Test->TestTrue(
						TEXT("P3Nav cross-world reference read succeeds"),
						TryReadPieObjectProperties(Flagged, FlaggedResult, Code, Message)))
					{
						const TSharedPtr<FJsonObject> Ref =
							ObjectRefOf(ItemNamed(FlaggedResult, TEXT("Payload")));
						Test->TestTrue(
							TEXT("P3Nav cross-world reference is marked crossWorld"),
							ItemBool(Ref, TEXT("crossWorld")));
						Test->TestFalse(
							TEXT("P3Nav cross-world reference is not navigable"),
							ItemBool(Ref, TEXT("propertiesReadable")));
						Test->TestEqual(
							TEXT("P3Nav cross-world reference reports the other world key"),
							ItemString(Ref, TEXT("worldKey")),
							State->ClientContextId);
						Test->TestEqual(
							TEXT("P3Nav cross-world reference reports the other instance"),
							static_cast<int32>(ItemNumber(Ref, TEXT("pieInstanceId"))),
							State->ClientInstanceId);

						FObjectPropertyReadRequest Follow;
						Follow.Target.Actor.PieInstanceId = State->ServerInstanceId;
						Follow.Target.ObjectPath = ItemString(Ref, TEXT("path"));
						Follow.PropertyNames = { TEXT("bEnableWorldBoundsChecks") };
						TSharedPtr<FJsonObject> FollowResult;
						const bool bFollowOk = TryReadPieObjectProperties(
							Follow,
							FollowResult,
							Code,
							Message);
						Test->TestFalse(
							TEXT("P3Nav following a cross-world reference is refused"),
							bFollowOk);
						Test->TestEqual(
							TEXT("P3Nav following a cross-world reference returns pie-world-stale"),
							Code,
							FString(FailureCode(EFailure::WorldStale)));
						Test->TestFalse(
							TEXT("P3Nav followed cross-world read returns no result"),
							FollowResult.IsValid());
						CrossWorldEvidence = FlaggedResult;
					}
					NavComponent->Payload = Payload;
				}
				bCrossWorldVerified = !State->ClientObjectPath.IsEmpty();
			}

			// ---- not-found refusal --------------------------------------------
			bool bNotFoundVerified = false;
			{
				FObjectPropertyReadRequest Read;
				Read.Target.Actor.PieInstanceId = State->ServerInstanceId;
				Read.Target.ObjectPath = FString::Printf(TEXT("%s.NoSuchObject"), *State->ComponentPath);
				Read.PropertyNames = { TEXT("Gauge") };
				TSharedPtr<FJsonObject> Missing;
				const bool bOk = TryReadPieObjectProperties(Read, Missing, Code, Message);
				Test->TestFalse(TEXT("P3Nav unknown object path is refused"), bOk);
				Test->TestEqual(
					TEXT("P3Nav unknown object path returns pie-object-not-found"),
					Code,
					FString(FailureCode(EFailure::ObjectNotFound)));
				Test->TestFalse(
					TEXT("P3Nav unknown object path returns no result"),
					Missing.IsValid());

				// Property discovery shares the same resolver.
				FObjectPropertyListRequest List;
				List.Target.Actor.PieInstanceId = State->ServerInstanceId;
				List.Target.ObjectPath = TEXT("/Game/NoSuchMap.NoSuchMap:PersistentLevel.Ghost");
				TSharedPtr<FJsonObject> MissingList;
				const bool bListOk = TryListPieObjectProperties(List, MissingList, Code, Message);
				Test->TestFalse(TEXT("P3Nav unknown object path discovery is refused"), bListOk);
				Test->TestEqual(
					TEXT("P3Nav unknown object path discovery returns pie-object-not-found"),
					Code,
					FString(FailureCode(EFailure::ObjectNotFound)));
				bNotFoundVerified = !bOk && !bListOk;
			}

			// ---- read-only proof ----------------------------------------------
			Test->TestEqual(TEXT("P3Nav fixture Health is unchanged"), Fixture->Health, 64.0f);
			Test->TestEqual(
				TEXT("P3Nav fixture Gauge is unchanged"),
				NavComponent->Gauge,
				12.5f);
			Test->TestTrue(
				TEXT("P3Nav fixture payload reference is unchanged"),
				NavComponent->Payload.Get() == static_cast<UObject*>(Payload));
			Test->TestEqual(
				TEXT("P3Nav payload Label is unchanged"),
				Payload->Label,
				FString(TEXT("hop-three")));
			Test->TestTrue(TEXT("P3Nav every hop is read-only"), bReadOnlyVerified);

			// ---- evidence ------------------------------------------------------
			TSharedRef<FJsonObject> Extra = MakeShared<FJsonObject>();
			Extra->SetNumberField(TEXT("pieContextCount"), State->ContextCount);
			Extra->SetNumberField(TEXT("serverPieInstanceId"), State->ServerInstanceId);
			Extra->SetNumberField(TEXT("clientPieInstanceId"), State->ClientInstanceId);
			Extra->SetStringField(TEXT("serverContextId"), State->ServerContextId);
			Extra->SetStringField(TEXT("clientContextId"), State->ClientContextId);
			Extra->SetStringField(TEXT("serverWorldPath"), State->ServerWorldPath);
			Extra->SetStringField(TEXT("actorPath"), State->ActorPath);
			Extra->SetStringField(TEXT("componentPath"), State->ComponentPath);
			Extra->SetStringField(TEXT("payloadPath"), State->PayloadPath);
			Extra->SetStringField(TEXT("clientObjectPath"), State->ClientObjectPath);
			Extra->SetBoolField(TEXT("hop1Verified"), bHop1Verified);
			Extra->SetBoolField(TEXT("hop2Verified"), bHop2Verified);
			Extra->SetBoolField(TEXT("hop3Verified"), bHop3Verified);
			Extra->SetBoolField(TEXT("noRecursionVerified"), bNoRecursionVerified);
			Extra->SetBoolField(TEXT("crossWorldVerified"), bCrossWorldVerified);
			Extra->SetBoolField(TEXT("notFoundVerified"), bNotFoundVerified);
			Extra->SetBoolField(TEXT("readOnlyVerified"), bReadOnlyVerified);
			Extra->SetStringField(TEXT("crossWorldCode"), FString(FailureCode(EFailure::WorldStale)));
			Extra->SetStringField(TEXT("notFoundCode"), FString(FailureCode(EFailure::ObjectNotFound)));
			if (Hop1.IsValid())
			{
				Extra->SetObjectField(TEXT("hop1"), Hop1.ToSharedRef());
			}
			if (Hop2.IsValid())
			{
				Extra->SetObjectField(TEXT("hop2"), Hop2.ToSharedRef());
			}
			if (Hop3.IsValid())
			{
				Extra->SetObjectField(TEXT("hop3"), Hop3.ToSharedRef());
			}
			if (CrossWorldEvidence.IsValid())
			{
				Extra->SetObjectField(TEXT("crossWorldReference"), CrossWorldEvidence.ToSharedRef());
			}

			TSharedRef<FJsonObject> Evidence = MakeShared<FJsonObject>();
			Evidence->SetStringField(TEXT("schemaVersion"), TEXT("ueak.p3pie.object-navigation/1.0"));
			Evidence->SetNumberField(
				TEXT("processId"),
				static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
			Evidence->SetStringField(TEXT("engineVersion"), FEngineVersion::Current().ToString());
			Evidence->SetStringField(TEXT("writtenAtUtc"), FDateTime::UtcNow().ToIso8601());
			Evidence->SetStringField(TEXT("source"), TEXT("native-automation-real-pie"));
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Field : Extra->Values)
			{
				Evidence->SetField(Field.Key, Field.Value);
			}
			const FString Path = FPaths::Combine(
				FPaths::ProjectSavedDir(),
				EvidenceRelativeDirectory,
				TEXT("p3pie-object-navigation-evidence.json"));
			Test->TestTrue(
				TEXT("P3Nav evidence is written"),
				WriteJson(Path, Evidence));

			RestorePlaySettings(State);
		}

		TSharedRef<FState> State;
	};
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPieObjectNavigationContractTest,
	"UEAgentKit.P0Pie.ObjectNavigationContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPieObjectNavigationContractTest::RunTest(const FString& Parameters)
{
	using namespace UEAgentKitPieRuntime;

	ResetPieInstanceHistoryForTests();

	const FString ObjectNotFoundCode(FailureCode(EFailure::ObjectNotFound));
	const FString InvalidCode(FailureCode(EFailure::InvalidRequest));
	const FString NotRunningCode(FailureCode(EFailure::InstanceNotRunning));
	const FString WorldStaleCode(FailureCode(EFailure::WorldStale));

	TestEqual(TEXT("P3Nav object-not-found wire code"), ObjectNotFoundCode, FString(TEXT("pie-object-not-found")));
	TestTrue(
		TEXT("P3Nav object-not-found code is distinct from the rest of the family"),
		ObjectNotFoundCode != InvalidCode
			&& ObjectNotFoundCode != NotRunningCode
			&& ObjectNotFoundCode != WorldStaleCode
			&& ObjectNotFoundCode != FString(FailureCode(EFailure::ActorNotFound))
			&& ObjectNotFoundCode != FString(FailureCode(EFailure::ComponentNotFound)));
	TestFalse(
		TEXT("P3Nav object-not-found is not classified as STALE"),
		IsStaleFailure(EFailure::ObjectNotFound));
	TestTrue(
		TEXT("P3Nav world-stale keeps the STALE classification"),
		IsStaleFailure(EFailure::WorldStale));

	TSharedPtr<FJsonObject> Result;
	FString Code;
	FString Message;

	auto BaseRead = []()
	{
		FObjectPropertyReadRequest Request;
		Request.Target.Actor.PieInstanceId = 0;
		Request.PropertyNames = { TEXT("Health") };
		return Request;
	};

	// ---- target shape policy (validated before any world resolution) ---------
	{
		FObjectPropertyReadRequest Read = BaseRead();
		Read.Target.ObjectPath = TEXT("Game/X.X");
		TestFalse(
			TEXT("P3Nav objectPath without a leading slash is rejected"),
			TryReadPieObjectProperties(Read, Result, Code, Message));
		TestEqual(TEXT("P3Nav malformed objectPath returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest Read = BaseRead();
		Read.Target.ObjectPath = TEXT("/Game/X");
		TestFalse(
			TEXT("P3Nav objectPath without an object part is rejected"),
			TryReadPieObjectProperties(Read, Result, Code, Message));
		TestEqual(TEXT("P3Nav objectless path returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest Read = BaseRead();
		Read.Target.ObjectPath = TEXT("/Game/X.X..Y");
		TestFalse(
			TEXT("P3Nav objectPath with a parent-directory segment is rejected"),
			TryReadPieObjectProperties(Read, Result, Code, Message));
		TestEqual(TEXT("P3Nav traversal path returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest Read = BaseRead();
		Read.Target.ObjectPath = FString::ChrN(MaxReferencedObjectPathChars + 1, TEXT('A'));
		TestFalse(
			TEXT("P3Nav over-long objectPath is rejected"),
			TryReadPieObjectProperties(Read, Result, Code, Message));
		TestEqual(TEXT("P3Nav over-long objectPath returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest Read = BaseRead();
		Read.Target.ObjectPath = TEXT("/Game/X.X:PersistentLevel.Actor");
		Read.Target.Actor.ActorName = TEXT("P3NavActor");
		TestFalse(
			TEXT("P3Nav objectPath combined with an Actor identity is rejected"),
			TryReadPieObjectProperties(Read, Result, Code, Message));
		TestEqual(TEXT("P3Nav objectPath plus Actor identity returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest Read = BaseRead();
		Read.Target.ObjectPath = TEXT("/Game/X.X:PersistentLevel.Actor.Comp");
		Read.Target.ComponentName = TEXT("Comp");
		TestFalse(
			TEXT("P3Nav objectPath combined with a Component identity is rejected"),
			TryReadPieObjectProperties(Read, Result, Code, Message));
		TestEqual(TEXT("P3Nav objectPath plus Component identity returns pie-invalid-parameters"), Code, InvalidCode);
	}
	{
		FObjectPropertyReadRequest Read = BaseRead();
		Read.Target.ObjectPath = TEXT("/Game/X.X:PersistentLevel.Actor");
		Read.Target.Actor.PieInstanceId = -1;
		TestFalse(
			TEXT("P3Nav objectPath without an exact instance is rejected"),
			TryReadPieObjectProperties(Read, Result, Code, Message));
		TestEqual(TEXT("P3Nav objectPath without instance returns pie-invalid-parameters"), Code, InvalidCode);
	}

	// ---- valid target, PIE stopped ------------------------------------------
	{
		FObjectPropertyReadRequest Read = BaseRead();
		Read.Target.ObjectPath = TEXT("/Game/X.X:PersistentLevel.Actor");
		TestFalse(
			TEXT("P3Nav objectPath target without PIE is rejected"),
			TryReadPieObjectProperties(Read, Result, Code, Message));
		TestEqual(TEXT("P3Nav objectPath without PIE returns pie-not-running"), Code, NotRunningCode);
	}
	{
		FObjectPropertyListRequest List;
		List.Target.Actor.PieInstanceId = 0;
		List.Target.ObjectPath = TEXT("/Game/X.X:PersistentLevel.Actor.Comp.Payload");
		TestFalse(
			TEXT("P3Nav objectPath discovery without PIE is rejected"),
			TryListPieObjectProperties(List, Result, Code, Message));
		TestEqual(TEXT("P3Nav discovery without PIE returns pie-not-running"), Code, NotRunningCode);
	}

	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitPieObjectNavigationRuntimeTest,
	"UEAgentKit.P0Pie.ObjectNavigationRuntime",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitPieObjectNavigationRuntimeTest::RunTest(const FString& Parameters)
{
	TSharedRef<UEAgentKitP3ObjectNav::FState> State =
		MakeShared<UEAgentKitP3ObjectNav::FState>();
	State->Test = this;

	ULevelEditorPlaySettings* PlaySettings = GetMutableDefault<ULevelEditorPlaySettings>();
	if (!TestNotNull(TEXT("P3Nav PIE play settings are available"), PlaySettings))
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
	ADD_LATENT_AUTOMATION_COMMAND(UEAgentKitP3ObjectNav::FWaitForNavContexts(State));
	ADD_LATENT_AUTOMATION_COMMAND(UEAgentKitP3ObjectNav::FDwellBeforeStopping(1.0));
	ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS

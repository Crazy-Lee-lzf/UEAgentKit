#include "PieRuntimeInspection.h"

#include "EditorBridge.h"
#include "EditorBridgeHandlerUtils.h"

#include "Components/ActorComponent.h"
#include "Components/MeshComponent.h"
#include "Components/PrimitiveComponent.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/Engine.h"
#include "Engine/Blueprint.h"
#include "Engine/BlueprintGeneratedClass.h"
#include "Engine/GameInstance.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "GameFramework/Pawn.h"
#include "Materials/MaterialInterface.h"
#include "Misc/DateTime.h"
#include "UObject/EnumProperty.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

namespace UEAgentKitPieRuntime
{
	namespace
	{
		FString NetModeWireName(const ENetMode NetMode)
		{
			switch (NetMode)
			{
			case NM_Standalone:
				return TEXT("standalone");
			case NM_DedicatedServer:
				return TEXT("dedicatedServer");
			case NM_ListenServer:
				return TEXT("listenServer");
			case NM_Client:
				return TEXT("client");
			default:
				return TEXT("unknown");
			}
		}

		FString NetModeRoleName(const ENetMode NetMode)
		{
			if (NetMode == NM_Standalone)
			{
				return TEXT("standalone");
			}
			return NetMode == NM_Client ? TEXT("client") : TEXT("server");
		}

		FString NetRoleEnumName(const ENetRole Role)
		{
			const UEnum* RoleEnum = StaticEnum<ENetRole>();
			if (RoleEnum == nullptr)
			{
				return FString();
			}
			return RoleEnum->GetNameStringByValue(static_cast<int64>(Role));
		}

		FString NetRoleShortName(const ENetRole Role)
		{
			FString Name = NetRoleEnumName(Role);
			if (Name.StartsWith(TEXT("ROLE_"), ESearchCase::CaseSensitive))
			{
				Name.RightChopInline(5);
			}
			return Name.ToLower();
		}

		FString CompactGuidDigits(const FGuid& Guid)
		{
			return Guid.ToString(EGuidFormats::Digits).ToLower();
		}

		FString NormalizeGuidText(const FString& Text)
		{
			FString Compact = Text;
			Compact.ReplaceInline(TEXT("-"), TEXT(""));
			return Compact.ToLower();
		}

		TSharedRef<FJsonObject> VectorJson(const FVector& Vector)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetNumberField(TEXT("x"), Vector.X);
			Item->SetNumberField(TEXT("y"), Vector.Y);
			Item->SetNumberField(TEXT("z"), Vector.Z);
			return Item;
		}

		TSharedRef<FJsonObject> RotatorJson(const FRotator& Rotator)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetNumberField(TEXT("pitch"), Rotator.Pitch);
			Item->SetNumberField(TEXT("yaw"), Rotator.Yaw);
			Item->SetNumberField(TEXT("roll"), Rotator.Roll);
			return Item;
		}

		/**
		 * Game-thread-only observation state.  The Editor Bridge pumps every
		 * request on the game thread, so no locking is introduced here.
		 */
		struct FPieInstanceHistory
		{
			TSet<int32> LiveInstanceIds;
			TArray<int32> RetiredInstanceIds;
		};

		FPieInstanceHistory& GetPieInstanceHistory()
		{
			static FPieInstanceHistory History;
			return History;
		}

		bool PathEqualsExact(const FString& Left, const FString& Right)
		{
			return Left.Equals(Right, ESearchCase::CaseSensitive);
		}

		FString JoinCandidatePaths(const TArray<FString>& Candidates)
		{
			FString Joined;
			for (const FString& Candidate : Candidates)
			{
				if (!Joined.IsEmpty())
				{
					Joined += TEXT(", ");
				}
				Joined += Candidate;
			}
			return Joined;
		}
	}

	const TCHAR* LookupStatusCode(const ELookupStatus Status)
	{
		switch (Status)
		{
		case ELookupStatus::NotRunning:
			return TEXT("pie-not-running");
		case ELookupStatus::NotFound:
			return TEXT("pie-instance-not-found");
		case ELookupStatus::Stale:
			return TEXT("pie-instance-stale");
		case ELookupStatus::Matched:
		default:
			return TEXT("");
		}
	}

	const TCHAR* FailureCode(const EFailure Failure)
	{
		switch (Failure)
		{
		case EFailure::InvalidRequest:
			return TEXT("pie-invalid-parameters");
		case EFailure::InstanceNotRunning:
			return TEXT("pie-not-running");
		case EFailure::InstanceNotFound:
			return TEXT("pie-instance-not-found");
		case EFailure::InstanceStale:
			return TEXT("pie-instance-stale");
		case EFailure::InstanceAmbiguous:
			return TEXT("pie-instance-ambiguous");
		case EFailure::WorldStale:
			return TEXT("pie-world-stale");
		case EFailure::ActorNotFound:
			return TEXT("pie-actor-not-found");
		case EFailure::ActorAmbiguous:
			return TEXT("pie-actor-ambiguous");
		case EFailure::ActorStale:
			return TEXT("pie-actor-stale");
		case EFailure::ComponentNotFound:
			return TEXT("pie-component-not-found");
		case EFailure::ComponentAmbiguous:
			return TEXT("pie-component-ambiguous");
		case EFailure::ComponentStale:
			return TEXT("pie-component-stale");
		case EFailure::ContextScanTruncated:
			return TEXT("pie-context-scan-truncated");
		case EFailure::ScanTruncated:
			return TEXT("pie-actor-scan-truncated");
		case EFailure::None:
		default:
			return TEXT("");
		}
	}

	bool IsStaleFailure(const EFailure Failure)
	{
		return Failure == EFailure::InstanceStale
			|| Failure == EFailure::WorldStale
			|| Failure == EFailure::ActorStale;
	}

	ELookupStatus ClassifyInstanceLookup(
		const int32 RequestedInstanceId,
		const TArray<int32>& LiveInstanceIds,
		const TArray<int32>& RetiredInstanceIds)
	{
		if (RequestedInstanceId >= 0 && LiveInstanceIds.Contains(RequestedInstanceId))
		{
			return ELookupStatus::Matched;
		}
		if (RequestedInstanceId >= 0 && RetiredInstanceIds.Contains(RequestedInstanceId))
		{
			return ELookupStatus::Stale;
		}
		if (LiveInstanceIds.Num() == 0)
		{
			return ELookupStatus::NotRunning;
		}
		return ELookupStatus::NotFound;
	}

	void ObserveLivePieInstances(const TArray<int32>& LiveInstanceIds)
	{
		FPieInstanceHistory& History = GetPieInstanceHistory();
		TSet<int32> NewLiveInstanceIds;
		for (const int32 InstanceId : LiveInstanceIds)
		{
			if (InstanceId >= 0)
			{
				NewLiveInstanceIds.Add(InstanceId);
			}
		}
		for (const int32 PreviousInstanceId : History.LiveInstanceIds)
		{
			if (!NewLiveInstanceIds.Contains(PreviousInstanceId))
			{
				History.RetiredInstanceIds.Remove(PreviousInstanceId);
				History.RetiredInstanceIds.Add(PreviousInstanceId);
			}
		}
		while (History.RetiredInstanceIds.Num() > RetiredPieInstanceHistory)
		{
			History.RetiredInstanceIds.RemoveAt(0);
		}
		History.LiveInstanceIds = MoveTemp(NewLiveInstanceIds);
	}

	void GetRetiredPieInstances(TArray<int32>& OutRetiredInstanceIds)
	{
		OutRetiredInstanceIds = GetPieInstanceHistory().RetiredInstanceIds;
	}

	void ResetPieInstanceHistoryForTests()
	{
		FPieInstanceHistory& History = GetPieInstanceHistory();
		History.LiveInstanceIds.Reset();
		History.RetiredInstanceIds.Reset();
	}

	void CollectPieWorldContexts(TArray<FWorldContextSnapshot>& OutContexts, bool& bOutTruncated)
	{
		OutContexts.Reset();
		bOutTruncated = false;
		TArray<int32> LiveInstanceIds;
		if (GEditor == nullptr)
		{
			ObserveLivePieInstances(LiveInstanceIds);
			return;
		}

		const TIndirectArray<FWorldContext>& WorldContexts = GEditor->GetWorldContexts();
		int32 ContextIndex = 0;
		for (const FWorldContext& Context : WorldContexts)
		{
			const int32 CurrentIndex = ContextIndex++;
			if (Context.WorldType != EWorldType::PIE)
			{
				continue;
			}
			UWorld* World = Context.World();
			if (World == nullptr)
			{
				continue;
			}
			if (OutContexts.Num() >= MaxWorldContextScan)
			{
				bOutTruncated = true;
				break;
			}

			FWorldContextSnapshot Snapshot;
			Snapshot.World = World;
			Snapshot.ContextIndex = CurrentIndex;
			Snapshot.PieInstanceId = Context.PIEInstance;
			if (Snapshot.PieInstanceId < 0 && World->GetOutermost() != nullptr)
			{
				Snapshot.PieInstanceId = World->GetOutermost()->GetPIEInstanceID();
			}
			const ENetMode NetMode = World->GetNetMode();
			Snapshot.NetMode = static_cast<int32>(NetMode);
			Snapshot.NetModeName = NetModeWireName(NetMode);
			Snapshot.ServerOrClient = NetModeRoleName(NetMode);
			Snapshot.bIsServer = Snapshot.ServerOrClient == TEXT("server");
			Snapshot.bIsClient = Snapshot.ServerOrClient == TEXT("client");
			Snapshot.bIsStandalone = NetMode == NM_Standalone;
			Snapshot.bIsDedicatedServer = NetMode == NM_DedicatedServer;
			Snapshot.bIsListenServer = NetMode == NM_ListenServer;
			Snapshot.bIsPrimaryPieInstance = Context.bIsPrimaryPIEInstance;
			Snapshot.bRunAsDedicated = Context.RunAsDedicated;
			Snapshot.WorldPath = World->GetPathName();
			Snapshot.WorldName = World->GetMapName();
			Snapshot.PackagePath = World->GetOutermost() != nullptr ? World->GetOutermost()->GetName() : FString();
			Snapshot.WorldType = UEAgentKitEditorBridgePrivate::GetWorldTypeName(World->WorldType);
			Snapshot.ContextId = FString::Printf(
				TEXT("pie-%d-%s"),
				Snapshot.PieInstanceId,
				*Snapshot.NetModeName);
			Snapshot.bHasBegunPlay = World->HasBegunPlay();
			Snapshot.bIsPaused = World->IsPaused();
			Snapshot.TimeSeconds = World->GetTimeSeconds();
			Snapshot.LocalPlayerCount = World->GetGameInstance() != nullptr
				? World->GetGameInstance()->GetNumLocalPlayers()
				: 0;

			LiveInstanceIds.AddUnique(Snapshot.PieInstanceId);
			OutContexts.Add(MoveTemp(Snapshot));
		}
		ObserveLivePieInstances(LiveInstanceIds);
	}

	TSharedRef<FJsonObject> DescribeWorldContext(const FWorldContextSnapshot& Snapshot)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("contextId"), Snapshot.ContextId);
		Item->SetNumberField(TEXT("pieInstanceId"), Snapshot.PieInstanceId);
		Item->SetNumberField(TEXT("worldContextIndex"), Snapshot.ContextIndex);
		Item->SetStringField(TEXT("worldPath"), Snapshot.WorldPath);
		Item->SetStringField(TEXT("worldName"), Snapshot.WorldName);
		Item->SetStringField(TEXT("packagePath"), Snapshot.PackagePath);
		Item->SetStringField(TEXT("worldType"), Snapshot.WorldType);
		Item->SetStringField(TEXT("netModeName"), Snapshot.NetModeName);
		Item->SetNumberField(TEXT("netMode"), Snapshot.NetMode);
		Item->SetStringField(TEXT("serverOrClient"), Snapshot.ServerOrClient);
		Item->SetBoolField(TEXT("isServer"), Snapshot.bIsServer);
		Item->SetBoolField(TEXT("isClient"), Snapshot.bIsClient);
		Item->SetBoolField(TEXT("isStandalone"), Snapshot.bIsStandalone);
		Item->SetBoolField(TEXT("isDedicatedServer"), Snapshot.bIsDedicatedServer);
		Item->SetBoolField(TEXT("isListenServer"), Snapshot.bIsListenServer);
		Item->SetBoolField(TEXT("isPrimaryPieInstance"), Snapshot.bIsPrimaryPieInstance);
		Item->SetBoolField(TEXT("runAsDedicated"), Snapshot.bRunAsDedicated);
		Item->SetNumberField(TEXT("localPlayerCount"), Snapshot.LocalPlayerCount);
		Item->SetBoolField(TEXT("hasBegunPlay"), Snapshot.bHasBegunPlay);
		Item->SetBoolField(TEXT("isPaused"), Snapshot.bIsPaused);
		Item->SetNumberField(TEXT("timeSeconds"), Snapshot.TimeSeconds);
		return Item;
	}

	TSharedRef<FJsonObject> BuildWorldContextsJson(const int32 MaxContexts)
	{
		const int32 EffectiveMax = FMath::Clamp(MaxContexts, 1, MaxPieWorldContexts);
		TArray<FWorldContextSnapshot> Contexts;
		bool bScanTruncated = false;
		CollectPieWorldContexts(Contexts, bScanTruncated);

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		const FString PieState = UEAgentKitEditorBridgePrivate::GetPieStateName();
		const bool bRunning = Contexts.Num() > 0;
		Result->SetStringField(TEXT("state"), PieState);
		Result->SetBoolField(TEXT("running"), bRunning);
		Result->SetBoolField(TEXT("simulatingInEditor"), PieState.Equals(TEXT("simulating"), ESearchCase::CaseSensitive));
		Result->SetBoolField(TEXT("readOnly"), true);
		Result->SetBoolField(TEXT("requiresDatabase"), false);
		Result->SetStringField(
			TEXT("code"),
			bRunning ? FString() : FString(LookupStatusCode(ELookupStatus::NotRunning)));
		Result->SetNumberField(TEXT("maxContexts"), EffectiveMax);
		Result->SetNumberField(TEXT("contextCount"), Contexts.Num());

		int32 ServerCount = 0;
		int32 ClientCount = 0;
		int32 StandaloneCount = 0;
		TArray<TSharedPtr<FJsonValue>> Items;
		TArray<TSharedPtr<FJsonValue>> InstanceIds;
		TSet<int32> SeenInstanceIds;
		for (const FWorldContextSnapshot& Snapshot : Contexts)
		{
			if (Snapshot.bIsServer)
			{
				++ServerCount;
			}
			else if (Snapshot.bIsClient)
			{
				++ClientCount;
			}
			else
			{
				++StandaloneCount;
			}
			if (!SeenInstanceIds.Contains(Snapshot.PieInstanceId))
			{
				SeenInstanceIds.Add(Snapshot.PieInstanceId);
				InstanceIds.Add(MakeShared<FJsonValueNumber>(Snapshot.PieInstanceId));
			}
			if (Items.Num() < EffectiveMax)
			{
				Items.Add(MakeShared<FJsonValueObject>(DescribeWorldContext(Snapshot)));
			}
		}

		Result->SetNumberField(TEXT("serverCount"), ServerCount);
		Result->SetNumberField(TEXT("clientCount"), ClientCount);
		Result->SetNumberField(TEXT("standaloneCount"), StandaloneCount);
		Result->SetNumberField(TEXT("returnedCount"), Items.Num());
		Result->SetBoolField(TEXT("truncated"), bScanTruncated || Contexts.Num() > Items.Num());
		Result->SetBoolField(TEXT("identitiesUnique"), Contexts.Num() == SeenInstanceIds.Num());
		Result->SetArrayField(TEXT("pieInstanceIds"), InstanceIds);
		Result->SetArrayField(TEXT("items"), Items);
		return Result;
	}

	namespace
	{
		TSharedRef<FJsonObject> DescribeActor(
			const FWorldContextSnapshot& Snapshot,
			AActor* Actor,
			const FActorInspectionRequest& Request,
			const TCHAR* ResolvedBy)
		{
			const int32 MaxComponents = FMath::Clamp(Request.MaxComponents, 1, MaxActorComponents);
			const int32 MaxAssets = FMath::Clamp(Request.MaxComponentAssets, 1, MaxComponentAssets);

			TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
			Result->SetBoolField(TEXT("readOnly"), true);
			Result->SetBoolField(TEXT("requiresDatabase"), false);
			Result->SetStringField(TEXT("resolvedBy"), ResolvedBy);
			Result->SetStringField(TEXT("pieState"), UEAgentKitEditorBridgePrivate::GetPieStateName());
			Result->SetNumberField(TEXT("pieInstanceId"), Snapshot.PieInstanceId);
			Result->SetStringField(TEXT("contextId"), Snapshot.ContextId);
			Result->SetStringField(TEXT("worldPath"), Snapshot.WorldPath);
			Result->SetStringField(TEXT("worldName"), Snapshot.WorldName);
			Result->SetStringField(TEXT("netModeName"), Snapshot.NetModeName);
			Result->SetNumberField(TEXT("netMode"), Snapshot.NetMode);
			Result->SetStringField(TEXT("serverOrClient"), Snapshot.ServerOrClient);

			Result->SetStringField(TEXT("actorPath"), Actor->GetPathName());
			Result->SetStringField(TEXT("actorName"), Actor->GetName());
			Result->SetStringField(TEXT("actorLabel"), Actor->GetActorLabel(false));
			Result->SetStringField(
				TEXT("classPath"),
				Actor->GetClass() != nullptr ? Actor->GetClass()->GetPathName() : FString());
			Result->SetStringField(
				TEXT("levelPath"),
				Actor->GetLevel() != nullptr ? Actor->GetLevel()->GetPathName() : FString());
			Result->SetStringField(
				TEXT("packagePath"),
				Actor->GetOutermost() != nullptr ? Actor->GetOutermost()->GetName() : FString());
			Result->SetStringField(TEXT("actorGuid"), Actor->GetActorGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
			Result->SetStringField(
				TEXT("actorInstanceGuid"),
				Actor->GetActorInstanceGuid().ToString(EGuidFormats::DigitsWithHyphensLower));
			Result->SetStringField(
				TEXT("ownerPath"),
				Actor->GetOwner() != nullptr ? Actor->GetOwner()->GetPathName() : FString());
			Result->SetStringField(
				TEXT("instigatorPath"),
				Actor->GetInstigator() != nullptr ? Actor->GetInstigator()->GetPathName() : FString());

			TArray<TSharedPtr<FJsonValue>> Hierarchy;
			for (const UClass* Class = Actor->GetClass();
				Class != nullptr && Hierarchy.Num() < MaxClassHierarchy;
				Class = Class->GetSuperClass())
			{
				Hierarchy.Add(MakeShared<FJsonValueString>(Class->GetPathName()));
			}
			Result->SetArrayField(TEXT("classHierarchy"), Hierarchy);

			const FTransform Transform = Actor->GetActorTransform();
			TSharedRef<FJsonObject> TransformJson = MakeShared<FJsonObject>();
			TransformJson->SetObjectField(TEXT("location"), VectorJson(Transform.GetLocation()));
			TransformJson->SetObjectField(TEXT("rotation"), RotatorJson(Transform.Rotator()));
			TransformJson->SetObjectField(TEXT("scale"), VectorJson(Transform.GetScale3D()));
			Result->SetObjectField(TEXT("transform"), TransformJson);

			const ENetRole LocalRole = Actor->GetLocalRole();
			const ENetRole RemoteRole = Actor->GetRemoteRole();
			TSharedRef<FJsonObject> NetRoleJson = MakeShared<FJsonObject>();
			NetRoleJson->SetStringField(TEXT("localRole"), NetRoleEnumName(LocalRole));
			NetRoleJson->SetStringField(TEXT("localRoleName"), NetRoleShortName(LocalRole));
			NetRoleJson->SetStringField(TEXT("remoteRole"), NetRoleEnumName(RemoteRole));
			NetRoleJson->SetStringField(TEXT("remoteRoleName"), NetRoleShortName(RemoteRole));
			NetRoleJson->SetBoolField(TEXT("hasAuthority"), Actor->HasAuthority());
			NetRoleJson->SetStringField(TEXT("netModeName"), Snapshot.NetModeName);
			NetRoleJson->SetStringField(TEXT("serverOrClient"), Snapshot.ServerOrClient);
			NetRoleJson->SetBoolField(TEXT("isServer"), Snapshot.bIsServer);
			NetRoleJson->SetBoolField(TEXT("isClient"), Snapshot.bIsClient);
			Result->SetObjectField(TEXT("netRole"), NetRoleJson);

			TArray<UActorComponent*> Components;
			Actor->GetComponents(Components);
			// TArray::Sort dereferences pointers, so the predicate receives components.
			Components.Sort([](const UActorComponent& Left, const UActorComponent& Right)
			{
				return Left.GetName() < Right.GetName();
			});

			TArray<TSharedPtr<FJsonValue>> ComponentItems;
			for (UActorComponent* Component : Components)
			{
				if (Component == nullptr || ComponentItems.Num() >= MaxComponents)
				{
					continue;
				}
				TArray<FString> Assets;
				bool bAssetsTruncated = false;
				auto AddAssetPath = [&Assets, &bAssetsTruncated, MaxAssets](const FString& AssetPath)
				{
					if (AssetPath.IsEmpty() || Assets.Contains(AssetPath))
					{
						return;
					}
					if (Assets.Num() >= MaxAssets)
					{
						bAssetsTruncated = true;
						return;
					}
					Assets.Add(AssetPath);
				};

				if (const UStaticMeshComponent* StaticMeshComponent = Cast<UStaticMeshComponent>(Component))
				{
					if (const UStaticMesh* StaticMesh = StaticMeshComponent->GetStaticMesh())
					{
						AddAssetPath(StaticMesh->GetPathName());
					}
				}
				if (const USkeletalMeshComponent* SkeletalMeshComponent = Cast<USkeletalMeshComponent>(Component))
				{
					if (const USkeletalMesh* SkeletalMesh = SkeletalMeshComponent->GetSkeletalMeshAsset())
					{
						AddAssetPath(SkeletalMesh->GetPathName());
					}
				}
				if (const UMeshComponent* MeshComponent = Cast<UMeshComponent>(Component))
				{
					const int32 MaterialCount = MeshComponent->GetNumMaterials();
					for (int32 Index = 0; Index < MaterialCount && !bAssetsTruncated; ++Index)
					{
						if (const UMaterialInterface* Material = MeshComponent->GetMaterial(Index))
						{
							AddAssetPath(Material->GetPathName());
						}
					}
				}

				TSharedRef<FJsonObject> ComponentJson = MakeShared<FJsonObject>();
				ComponentJson->SetStringField(TEXT("name"), Component->GetName());
				ComponentJson->SetStringField(TEXT("objectPath"), Component->GetPathName());
				ComponentJson->SetStringField(
					TEXT("classPath"),
					Component->GetClass() != nullptr ? Component->GetClass()->GetPathName() : FString());
				ComponentJson->SetBoolField(TEXT("isRegistered"), Component->IsRegistered());
				ComponentJson->SetBoolField(TEXT("isActive"), Component->IsActive());
				ComponentJson->SetBoolField(TEXT("assetsTruncated"), bAssetsTruncated);
				TArray<TSharedPtr<FJsonValue>> AssetValues;
				for (const FString& AssetPath : Assets)
				{
					AssetValues.Add(MakeShared<FJsonValueString>(AssetPath));
				}
				ComponentJson->SetArrayField(TEXT("assets"), AssetValues);
				ComponentItems.Add(MakeShared<FJsonValueObject>(ComponentJson));
			}

			Result->SetNumberField(TEXT("componentCount"), Components.Num());
			Result->SetNumberField(TEXT("componentsReturned"), ComponentItems.Num());
			Result->SetBoolField(TEXT("componentsTruncated"), Components.Num() > ComponentItems.Num());
			Result->SetNumberField(TEXT("maxComponents"), MaxComponents);
			Result->SetNumberField(TEXT("maxComponentAssets"), MaxAssets);
			Result->SetArrayField(TEXT("components"), ComponentItems);
			return Result;
		}
	}

	EFailure ResolvePieActorExact(
		const FActorInspectionRequest& Request,
		FResolvedPieActor& OutResolved,
		FString& OutErrorMessage)
	{
		OutResolved = FResolvedPieActor();
		OutErrorMessage.Reset();

		auto Fail = [&OutErrorMessage](const EFailure Failure, const FString& Message)
		{
			OutErrorMessage = Message;
			return Failure;
		};

		if (Request.PieInstanceId < 0)
		{
			return Fail(
				EFailure::InvalidRequest,
				TEXT("pieInstanceId must be the exact PIE instance id reported by editor.getPieWorldContexts (0 is the primary instance)."));
		}
		int32 IdentifierCount = 0;
		IdentifierCount += Request.ActorPath.IsEmpty() ? 0 : 1;
		IdentifierCount += Request.ActorGuid.IsEmpty() ? 0 : 1;
		IdentifierCount += Request.ActorName.IsEmpty() ? 0 : 1;
		IdentifierCount += Request.ActorLabel.IsEmpty() ? 0 : 1;
		if (IdentifierCount != 1)
		{
			return Fail(
				EFailure::InvalidRequest,
				TEXT("Provide exactly one Actor identity: actorPath, actorGuid, actorName, or actorLabel."));
		}

		TArray<FWorldContextSnapshot> Contexts;
		bool bContextScanTruncated = false;
		CollectPieWorldContexts(Contexts, bContextScanTruncated);
		if (bContextScanTruncated)
		{
			return Fail(
				EFailure::ContextScanTruncated,
				FString::Printf(
					TEXT("The PIE world context scan reached its bounded limit of %d contexts, so the requested instance could not be verified."),
					MaxWorldContextScan));
		}

		TArray<int32> LiveInstanceIds;
		for (const FWorldContextSnapshot& Snapshot : Contexts)
		{
			LiveInstanceIds.AddUnique(Snapshot.PieInstanceId);
		}
		TArray<int32> RetiredInstanceIds;
		GetRetiredPieInstances(RetiredInstanceIds);

		const ELookupStatus Status = ClassifyInstanceLookup(Request.PieInstanceId, LiveInstanceIds, RetiredInstanceIds);
		if (Status == ELookupStatus::NotRunning)
		{
			return Fail(
				EFailure::InstanceNotRunning,
				FString::Printf(
					TEXT("PIE is not running in this Editor process, so PIE instance %d has no live world."),
					Request.PieInstanceId));
		}
		if (Status == ELookupStatus::Stale)
		{
			return Fail(
				EFailure::InstanceStale,
				FString::Printf(
					TEXT("PIE instance %d was observed earlier in this Editor process but is no longer running; re-query editor.getPieWorldContexts."),
					Request.PieInstanceId));
		}
		if (Status == ELookupStatus::NotFound)
		{
			return Fail(
				EFailure::InstanceNotFound,
				FString::Printf(
					TEXT("PIE instance %d is not one of the live PIE world contexts; re-query editor.getPieWorldContexts."),
					Request.PieInstanceId));
		}

		// A PIE instance id must identify exactly one live world.  If the engine
		// ever exposes the same id for two contexts (transient play/stop states),
		// fail closed instead of silently inspecting the first world.
		const FWorldContextSnapshot* Snapshot = nullptr;
		int32 MatchingContextCount = 0;
		FString MatchingContextSummary;
		for (const FWorldContextSnapshot& Candidate : Contexts)
		{
			if (Candidate.PieInstanceId != Request.PieInstanceId)
			{
				continue;
			}
			++MatchingContextCount;
			if (Snapshot == nullptr)
			{
				Snapshot = &Candidate;
			}
			if (MatchingContextSummary.Len() < 512)
			{
				if (!MatchingContextSummary.IsEmpty())
				{
					MatchingContextSummary += TEXT(", ");
				}
				MatchingContextSummary += FString::Printf(
					TEXT("%s (%s)"),
					*Candidate.WorldPath,
					*Candidate.NetModeName);
			}
		}
		if (MatchingContextCount > 1)
		{
			return Fail(
				EFailure::InstanceAmbiguous,
				FString::Printf(
					TEXT("PIE instance %d matches %d live world contexts (%s); the exact world is ambiguous, so nothing was inspected. Use the contextId from editor.getPieWorldContexts."),
					Request.PieInstanceId,
					MatchingContextCount,
					*MatchingContextSummary));
		}
		if (Snapshot == nullptr || Snapshot->World == nullptr)
		{
			return Fail(
				EFailure::InstanceNotFound,
				FString::Printf(TEXT("PIE instance %d no longer has a resolvable world."), Request.PieInstanceId));
		}
		if (!Request.WorldPath.IsEmpty() && !PathEqualsExact(Request.WorldPath, Snapshot->WorldPath))
		{
			return Fail(
				EFailure::WorldStale,
				FString::Printf(
					TEXT("PIE instance %d now resolves to world '%s', not the requested world '%s'; the instance was reused."),
					Request.PieInstanceId,
					*Snapshot->WorldPath,
					*Request.WorldPath));
		}

		AActor* Match = nullptr;
		int32 MatchCount = 0;
		TArray<FString> CandidatePaths;
		int32 ScannedActorCount = 0;
		bool bActorScanTruncated = false;
		const FString WantedGuid = NormalizeGuidText(Request.ActorGuid);
		for (TActorIterator<AActor> It(Snapshot->World); It; ++It)
		{
			++ScannedActorCount;
			if (ScannedActorCount > MaxScannedActors)
			{
				bActorScanTruncated = true;
				break;
			}
			AActor* Candidate = *It;
			if (Candidate == nullptr)
			{
				continue;
			}
			bool bMatches = false;
			if (!Request.ActorPath.IsEmpty())
			{
				bMatches = PathEqualsExact(Candidate->GetPathName(), Request.ActorPath);
			}
			else if (!Request.ActorGuid.IsEmpty())
			{
				bMatches = CompactGuidDigits(Candidate->GetActorGuid()) == WantedGuid
					|| CompactGuidDigits(Candidate->GetActorInstanceGuid()) == WantedGuid;
			}
			else if (!Request.ActorName.IsEmpty())
			{
				bMatches = PathEqualsExact(Candidate->GetName(), Request.ActorName);
			}
			else
			{
				bMatches = PathEqualsExact(Candidate->GetActorLabel(false), Request.ActorLabel);
			}
			if (!bMatches)
			{
				continue;
			}
			++MatchCount;
			if (Match == nullptr)
			{
				Match = Candidate;
			}
			if (CandidatePaths.Num() < MaxAmbiguousCandidates)
			{
				CandidatePaths.Add(Candidate->GetPathName());
			}
		}

		if (bActorScanTruncated)
		{
			return Fail(
				EFailure::ScanTruncated,
				FString::Printf(
					TEXT("The bounded Actor scan stopped after %d Actors in PIE instance %d before uniqueness could be proven; narrow the request or use actorGuid."),
					MaxScannedActors,
					Request.PieInstanceId));
		}
		if (MatchCount == 0 || Match == nullptr)
		{
			return Fail(
				EFailure::ActorNotFound,
				FString::Printf(
					TEXT("No Actor in PIE instance %d (world '%s', %s) matches the requested identity."),
					Request.PieInstanceId,
					*Snapshot->WorldPath,
					*Snapshot->NetModeName));
		}
		if (MatchCount > 1)
		{
			return Fail(
				EFailure::ActorAmbiguous,
				FString::Printf(
					TEXT("The Actor identity matches %d Actors in PIE instance %d; use actorPath or actorGuid. Bounded candidates: %s"),
					MatchCount,
					Request.PieInstanceId,
					*JoinCandidatePaths(CandidatePaths)));
		}
		if (Match->IsActorBeingDestroyed())
		{
			return Fail(
				EFailure::ActorNotFound,
				FString::Printf(
					TEXT("The matched Actor '%s' is being destroyed in PIE instance %d."),
					*Match->GetPathName(),
					Request.PieInstanceId));
		}
		if (!Request.ActorClassPath.IsEmpty()
			&& (Match->GetClass() == nullptr
				|| !PathEqualsExact(Match->GetClass()->GetPathName(), Request.ActorClassPath)))
		{
			return Fail(
				EFailure::ActorStale,
				FString::Printf(
					TEXT("The Actor '%s' in PIE instance %d is class '%s', not the expected '%s'."),
					*Match->GetPathName(),
					Request.PieInstanceId,
					Match->GetClass() != nullptr ? *Match->GetClass()->GetPathName() : TEXT(""),
					*Request.ActorClassPath));
		}

		OutResolved.Snapshot = *Snapshot;
		OutResolved.Actor = Match;
		return EFailure::None;
	}

	bool TryDescribePieActor(
		const FActorInspectionRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutResult.Reset();
		OutErrorCode.Reset();
		OutErrorMessage.Reset();

		FResolvedPieActor Resolved;
		FString Message;
		const EFailure Failure = ResolvePieActorExact(Request, Resolved, Message);
		if (Failure != EFailure::None)
		{
			OutErrorCode = FailureCode(Failure);
			OutErrorMessage = Message;
			return false;
		}

		const TCHAR* ResolvedBy = !Request.ActorPath.IsEmpty()
			? TEXT("actorPath")
			: (!Request.ActorGuid.IsEmpty()
				? TEXT("actorGuid")
				: (!Request.ActorName.IsEmpty() ? TEXT("actorName") : TEXT("actorLabel")));
		OutResult = DescribeActor(Resolved.Snapshot, Resolved.Actor, Request, ResolvedBy);
		return true;
	}

	namespace
	{
		/** JSON numbers are doubles; keep int64 exact beyond 2^53 by stringifying. */
		constexpr int64 MaxExactScalarInt64 = 9007199254740991LL;

		bool IsScalarPropertyName(const FString& Name)
		{
			if (Name.IsEmpty() || Name.Len() > MaxScalarPropertyNameChars)
			{
				return false;
			}
			const TCHAR First = Name[0];
			if (!(FChar::IsAlpha(First) || First == TEXT('_')))
			{
				return false;
			}
			for (int32 Index = 1; Index < Name.Len(); ++Index)
			{
				const TCHAR Character = Name[Index];
				if (!(FChar::IsAlnum(Character) || Character == TEXT('_')))
				{
					return false;
				}
			}
			return true;
		}

		/**
		 * Only reflected fields that an Editor/Blueprint reader is meant to see.
		 * Native-private properties and deprecated fields are rejected, and the
		 * caller must still name each property explicitly.
		 */
		bool IsExposedScalarProperty(const FProperty* Property)
		{
			const EPropertyFlags Flags = Property->GetPropertyFlags();
			if ((Flags & CPF_Deprecated) != 0)
			{
				return false;
			}
			if ((Flags & CPF_NativeAccessSpecifierPrivate) != 0)
			{
				return false;
			}
			return (Flags & (CPF_Edit | CPF_BlueprintVisible)) != 0;
		}

		FString TruncateTypeName(const FString& TypeName)
		{
			return TypeName.Len() > 64 ? TypeName.Left(64) : TypeName;
		}

		FString TruncateScalarString(
			const FString& Value,
			const int32 MaxChars,
			bool& bOutTruncated,
			int32& OutOriginalLength)
		{
			OutOriginalLength = Value.Len();
			if (OutOriginalLength <= MaxChars)
			{
				bOutTruncated = false;
				return Value;
			}
			bOutTruncated = true;
			int32 Cut = MaxChars;
			if (Cut > 0)
			{
				const TCHAR Last = Value[Cut - 1];
				if (Last >= 0xD800 && Last <= 0xDBFF)
				{
					--Cut;
				}
			}
			return Value.Left(Cut);
		}

		FString EnumShortName(const UEnum* Enum, const int64 Value)
		{
			if (Enum == nullptr)
			{
				return FString();
			}
			const FString FullName = Enum->GetNameStringByValue(Value);
			int32 SeparatorIndex = INDEX_NONE;
			if (FullName.FindLastChar(TEXT(':'), SeparatorIndex))
			{
				return FullName.Mid(SeparatorIndex + 1);
			}
			return FullName;
		}

		FString TruncateBoundedClassPath(const FString& Path, bool& bOutTruncated)
		{
			bOutTruncated = Path.Len() > MaxScalarClassPathChars;
			return bOutTruncated ? Path.Left(MaxScalarClassPathChars) : Path;
		}

		/**
		 * Additive declaration provenance from UE reflection only.
		 *
		 * Reports the reflected declaring UClass (which may be a base class) and,
		 * when the class object really is Blueprint-generated, its trusted asset
		 * path.  No filesystem path is derived, guessed or inspected, and no raw
		 * object address is exposed.  Only resolved properties get this metadata.
		 */
		void AddScalarPropertyProvenance(const FProperty* Property, const TSharedRef<FJsonObject>& Item)
		{
			const UClass* DeclaringClass = Property != nullptr ? Property->GetOwnerClass() : nullptr;
			if (DeclaringClass == nullptr)
			{
				Item->SetStringField(TEXT("declaringClassKind"), TEXT("unknown"));
				return;
			}

			bool bPathTruncated = false;
			Item->SetStringField(
				TEXT("declaringClassPath"),
				TruncateBoundedClassPath(DeclaringClass->GetPathName(), bPathTruncated));
			Item->SetBoolField(TEXT("declaringClassPathTruncated"), bPathTruncated);
			Item->SetStringField(TEXT("declaringClassName"), DeclaringClass->GetName());

			const bool bBlueprintGenerated =
				Cast<UBlueprintGeneratedClass>(DeclaringClass) != nullptr
				|| DeclaringClass->HasAnyClassFlags(CLASS_CompiledFromBlueprint);
			if (bBlueprintGenerated)
			{
				Item->SetStringField(TEXT("declaringClassKind"), TEXT("blueprint"));
#if WITH_EDITORONLY_DATA
				const UBlueprint* GeneratedBlueprint = DeclaringClass->ClassGeneratedBy != nullptr
					? Cast<UBlueprint>(DeclaringClass->ClassGeneratedBy)
					: nullptr;
				if (GeneratedBlueprint != nullptr)
				{
					bool bBlueprintPathTruncated = false;
					Item->SetStringField(
						TEXT("declaringBlueprintPath"),
						TruncateBoundedClassPath(
							GeneratedBlueprint->GetPathName(),
							bBlueprintPathTruncated));
					Item->SetBoolField(
						TEXT("declaringBlueprintPathTruncated"),
						bBlueprintPathTruncated);
				}
#endif
				return;
			}
			if (DeclaringClass->HasAnyClassFlags(CLASS_Native))
			{
				Item->SetStringField(TEXT("declaringClassKind"), TEXT("native"));
				return;
			}
			Item->SetStringField(TEXT("declaringClassKind"), TEXT("unknown"));
		}

		TSharedRef<FJsonObject> MakeScalarItem(const FProperty* Property)
		{
			const FString Name = Property != nullptr ? Property->GetName() : FString();
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("name"), Name);
			Item->SetStringField(TEXT("status"), TEXT("ok"));
			Item->SetBoolField(TEXT("valueReturned"), true);
			AddScalarPropertyProvenance(Property, Item);
			return Item;
		}

		TSharedRef<FJsonObject> MakeScalarFailure(
			const FString& Name,
			const TCHAR* Status,
			const TCHAR* Reason,
			const FString& DeclaredType)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			Item->SetStringField(TEXT("name"), Name);
			Item->SetStringField(TEXT("status"), Status);
			Item->SetStringField(TEXT("reason"), Reason);
			Item->SetBoolField(TEXT("valueReturned"), false);
			if (!DeclaredType.IsEmpty())
			{
				Item->SetStringField(TEXT("declaredType"), TruncateTypeName(DeclaredType));
			}
			return Item;
		}

		TSharedRef<FJsonObject> MakeEnumItem(const FProperty* Property, const UEnum* Enum, const int64 RawValue)
		{
			TSharedRef<FJsonObject> Item = MakeScalarItem(Property);
			const FString ShortName = EnumShortName(Enum, RawValue);
			Item->SetStringField(TEXT("type"), TEXT("enum"));
			Item->SetStringField(TEXT("enumName"), Enum != nullptr ? Enum->GetName() : FString());
			if (ShortName.IsEmpty())
			{
				Item->SetStringField(TEXT("value"), FString::Printf(TEXT("%lld"), RawValue));
				Item->SetBoolField(TEXT("enumEntryKnown"), false);
			}
			else
			{
				Item->SetStringField(TEXT("value"), ShortName);
				Item->SetStringField(TEXT("enumEntry"), Enum->GetNameStringByValue(RawValue));
				Item->SetBoolField(TEXT("enumEntryKnown"), true);
			}
			Item->SetNumberField(TEXT("numericValue"), static_cast<double>(RawValue));
			return Item;
		}

		/** Reads one supported immediate scalar; never traverses the object graph. */
		TSharedRef<FJsonObject> ReadScalarProperty(
			const FProperty* Property,
			const UObject* Object,
			const int32 MaxStringChars)
		{
			const FString DeclaredType = Property->GetCPPType();
			const void* ValuePtr = Property->ContainerPtrToValuePtr<void>(Object);
			if (ValuePtr == nullptr)
			{
				return MakeScalarFailure(Property->GetName(), TEXT("unknown"), TEXT("unreadable"), DeclaredType);
			}

			if (Property->IsA<FBoolProperty>())
			{
				const FBoolProperty* BoolProperty = static_cast<const FBoolProperty*>(Property);
				TSharedRef<FJsonObject> Item = MakeScalarItem(Property);
				Item->SetStringField(TEXT("type"), TEXT("bool"));
				Item->SetBoolField(TEXT("value"), BoolProperty->GetPropertyValue_InContainer(Object));
				return Item;
			}
			if (Property->IsA<FIntProperty>())
			{
				const FIntProperty* IntProperty = static_cast<const FIntProperty*>(Property);
				TSharedRef<FJsonObject> Item = MakeScalarItem(Property);
				Item->SetStringField(TEXT("type"), TEXT("int32"));
				Item->SetNumberField(TEXT("value"), static_cast<double>(IntProperty->GetPropertyValue_InContainer(Object)));
				return Item;
			}
			if (Property->IsA<FInt64Property>())
			{
				const FInt64Property* Int64Property = static_cast<const FInt64Property*>(Property);
				const int64 Value = Int64Property->GetPropertyValue_InContainer(Object);
				TSharedRef<FJsonObject> Item = MakeScalarItem(Property);
				Item->SetStringField(TEXT("type"), TEXT("int64"));
				if (Value >= -MaxExactScalarInt64 && Value <= MaxExactScalarInt64)
				{
					Item->SetNumberField(TEXT("value"), static_cast<double>(Value));
					Item->SetBoolField(TEXT("valueAsString"), false);
				}
				else
				{
					Item->SetStringField(TEXT("value"), FString::Printf(TEXT("%lld"), Value));
					Item->SetBoolField(TEXT("valueAsString"), true);
				}
				return Item;
			}
			if (Property->IsA<FUInt32Property>())
			{
				const FUInt32Property* UInt32Property = static_cast<const FUInt32Property*>(Property);
				TSharedRef<FJsonObject> Item = MakeScalarItem(Property);
				Item->SetStringField(TEXT("type"), TEXT("uint32"));
				Item->SetNumberField(TEXT("value"), static_cast<double>(UInt32Property->GetPropertyValue_InContainer(Object)));
				return Item;
			}
			if (Property->IsA<FFloatProperty>())
			{
				const FFloatProperty* FloatProperty = static_cast<const FFloatProperty*>(Property);
				const float Value = FloatProperty->GetPropertyValue_InContainer(Object);
				if (!FMath::IsFinite(Value))
				{
					return MakeScalarFailure(Property->GetName(), TEXT("unknown"), TEXT("non-finite"), DeclaredType);
				}
				TSharedRef<FJsonObject> Item = MakeScalarItem(Property);
				Item->SetStringField(TEXT("type"), TEXT("float32"));
				Item->SetNumberField(TEXT("value"), static_cast<double>(Value));
				return Item;
			}
			if (Property->IsA<FDoubleProperty>())
			{
				const FDoubleProperty* DoubleProperty = static_cast<const FDoubleProperty*>(Property);
				const double Value = DoubleProperty->GetPropertyValue_InContainer(Object);
				if (!FMath::IsFinite(Value))
				{
					return MakeScalarFailure(Property->GetName(), TEXT("unknown"), TEXT("non-finite"), DeclaredType);
				}
				TSharedRef<FJsonObject> Item = MakeScalarItem(Property);
				Item->SetStringField(TEXT("type"), TEXT("float64"));
				Item->SetNumberField(TEXT("value"), Value);
				return Item;
			}
			if (Property->IsA<FNameProperty>())
			{
				const FNameProperty* NameProperty = static_cast<const FNameProperty*>(Property);
				TSharedRef<FJsonObject> Item = MakeScalarItem(Property);
				Item->SetStringField(TEXT("type"), TEXT("name"));
				Item->SetStringField(TEXT("value"), NameProperty->GetPropertyValue_InContainer(Object).ToString());
				return Item;
			}
			if (Property->IsA<FStrProperty>())
			{
				const FStrProperty* StrProperty = static_cast<const FStrProperty*>(Property);
				bool bTruncated = false;
				int32 OriginalLength = 0;
				const FString Value = TruncateScalarString(
					StrProperty->GetPropertyValue_InContainer(Object),
					MaxStringChars,
					bTruncated,
					OriginalLength);
				TSharedRef<FJsonObject> Item = MakeScalarItem(Property);
				Item->SetStringField(TEXT("type"), TEXT("string"));
				Item->SetStringField(TEXT("value"), Value);
				Item->SetNumberField(TEXT("originalLength"), OriginalLength);
				Item->SetBoolField(TEXT("truncated"), bTruncated);
				return Item;
			}
			if (Property->IsA<FEnumProperty>())
			{
				const FEnumProperty* EnumProperty = static_cast<const FEnumProperty*>(Property);
				const FNumericProperty* Underlying = EnumProperty->GetUnderlyingProperty();
				const int64 RawValue = Underlying != nullptr
					? Underlying->GetSignedIntPropertyValue(ValuePtr)
					: 0;
				return MakeEnumItem(Property, EnumProperty->GetEnum(), RawValue);
			}
			if (Property->IsA<FByteProperty>())
			{
				const FByteProperty* ByteProperty = static_cast<const FByteProperty*>(Property);
				const uint8 RawValue = ByteProperty->GetPropertyValue_InContainer(Object);
				UEnum* ByteEnum = ByteProperty->GetIntPropertyEnum();
				if (ByteEnum != nullptr)
				{
					return MakeEnumItem(Property, ByteEnum, static_cast<int64>(RawValue));
				}
				TSharedRef<FJsonObject> Item = MakeScalarItem(Property);
				Item->SetStringField(TEXT("type"), TEXT("uint8"));
				Item->SetNumberField(TEXT("value"), static_cast<double>(RawValue));
				return Item;
			}
			return MakeScalarFailure(
				Property->GetName(),
				TEXT("unsupported-type"),
				TEXT("type-not-supported"),
				DeclaredType);
		}
	}

	bool TryReadPieScalars(
		const FScalarInspectionRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		FString& OutErrorCode,
		FString& OutErrorMessage)
	{
		OutResult.Reset();
		OutErrorCode.Reset();
		OutErrorMessage.Reset();

		auto Fail = [&OutErrorCode, &OutErrorMessage](const EFailure Failure, const FString& Message)
		{
			OutErrorCode = FailureCode(Failure);
			OutErrorMessage = Message;
			return false;
		};

		if (Request.PropertyNames.Num() <= 0 || Request.PropertyNames.Num() > MaxScalarProperties)
		{
			return Fail(
				EFailure::InvalidRequest,
				FString::Printf(
					TEXT("propertyNames must contain between 1 and %d identifier-only property names."),
					MaxScalarProperties));
		}
		TSet<FString> SeenNames;
		for (const FString& Name : Request.PropertyNames)
		{
			if (!IsScalarPropertyName(Name))
			{
				return Fail(
					EFailure::InvalidRequest,
					FString::Printf(
						TEXT("Property name '%s' is not a plain identifier; dotted paths, collections, methods and non-scalar fields are rejected."),
						*Name.Left(MaxScalarPropertyNameChars)));
			}
			if (SeenNames.Contains(Name))
			{
				return Fail(
					EFailure::InvalidRequest,
					FString::Printf(TEXT("Property name '%s' is requested more than once."), *Name));
			}
			SeenNames.Add(Name);
		}
		if (!Request.ComponentPath.IsEmpty() && !Request.ComponentName.IsEmpty())
		{
			return Fail(
				EFailure::InvalidRequest,
				TEXT("Provide at most one Component identity: componentPath or componentName."));
		}
		if (!Request.ComponentClassPath.IsEmpty()
			&& Request.ComponentPath.IsEmpty()
			&& Request.ComponentName.IsEmpty())
		{
			return Fail(
				EFailure::InvalidRequest,
				TEXT("componentClassPath requires componentPath or componentName."));
		}
		const int32 MaxChars = FMath::Clamp(
			Request.MaxStringChars,
			MinScalarStringChars,
			MaxScalarStringChars);

		FResolvedPieActor Resolved;
		FString Message;
		const EFailure ActorFailure = ResolvePieActorExact(Request.Actor, Resolved, Message);
		if (ActorFailure != EFailure::None)
		{
			OutErrorCode = FailureCode(ActorFailure);
			OutErrorMessage = Message;
			return false;
		}
		const FWorldContextSnapshot& Snapshot = Resolved.Snapshot;
		AActor* Actor = Resolved.Actor;
		UObject* Target = Actor;
		bool bComponentTarget = false;

		if (!Request.ComponentPath.IsEmpty() || !Request.ComponentName.IsEmpty())
		{
			TArray<UActorComponent*> Components;
			Actor->GetComponents(Components);
			UActorComponent* ComponentMatch = nullptr;
			int32 ComponentMatchCount = 0;
			TArray<FString> ComponentCandidates;
			for (UActorComponent* Candidate : Components)
			{
				if (Candidate == nullptr)
				{
					continue;
				}
				const bool bMatches = !Request.ComponentPath.IsEmpty()
					? PathEqualsExact(Candidate->GetPathName(), Request.ComponentPath)
					: PathEqualsExact(Candidate->GetName(), Request.ComponentName);
				if (!bMatches)
				{
					continue;
				}
				++ComponentMatchCount;
				if (ComponentMatch == nullptr)
				{
					ComponentMatch = Candidate;
				}
				if (ComponentCandidates.Num() < MaxAmbiguousCandidates)
				{
					ComponentCandidates.Add(Candidate->GetPathName());
				}
			}
			if (ComponentMatchCount == 0 || ComponentMatch == nullptr)
			{
				return Fail(
					EFailure::ComponentNotFound,
					FString::Printf(
						TEXT("No owned Component of Actor '%s' matches the requested identity in PIE instance %d."),
						*Actor->GetPathName(),
						Request.Actor.PieInstanceId));
			}
			if (ComponentMatchCount > 1)
			{
				return Fail(
					EFailure::ComponentAmbiguous,
					FString::Printf(
						TEXT("The Component identity matches %d owned Components in PIE instance %d; use componentPath. Bounded candidates: %s"),
						ComponentMatchCount,
						Request.Actor.PieInstanceId,
						*JoinCandidatePaths(ComponentCandidates)));
			}
			if (ComponentMatch->IsBeingDestroyed())
			{
				return Fail(
					EFailure::ComponentNotFound,
					FString::Printf(
						TEXT("The matched Component '%s' is being destroyed."),
						*ComponentMatch->GetPathName()));
			}
			if (!Request.ComponentClassPath.IsEmpty()
				&& (ComponentMatch->GetClass() == nullptr
					|| !PathEqualsExact(ComponentMatch->GetClass()->GetPathName(), Request.ComponentClassPath)))
			{
				return Fail(
					EFailure::ComponentStale,
					FString::Printf(
						TEXT("Component '%s' is class '%s', not the expected '%s'."),
						*ComponentMatch->GetPathName(),
						ComponentMatch->GetClass() != nullptr
							? *ComponentMatch->GetClass()->GetPathName()
							: TEXT(""),
						*Request.ComponentClassPath));
			}
			Target = ComponentMatch;
			bComponentTarget = true;
		}

		TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetBoolField(TEXT("readOnly"), true);
		Result->SetBoolField(TEXT("requiresDatabase"), false);
		Result->SetStringField(TEXT("source"), TEXT("pie-live"));
		Result->SetStringField(TEXT("sampledAtUtc"), FDateTime::UtcNow().ToIso8601());
		Result->SetNumberField(TEXT("pieInstanceId"), Snapshot.PieInstanceId);
		Result->SetStringField(TEXT("contextId"), Snapshot.ContextId);
		Result->SetStringField(TEXT("worldPath"), Snapshot.WorldPath);
		Result->SetStringField(TEXT("netModeName"), Snapshot.NetModeName);
		Result->SetStringField(TEXT("serverOrClient"), Snapshot.ServerOrClient);
		Result->SetStringField(TEXT("targetKind"), bComponentTarget ? TEXT("component") : TEXT("actor"));
		Result->SetStringField(TEXT("actorPath"), Actor->GetPathName());
		Result->SetStringField(TEXT("objectPath"), Target->GetPathName());
		Result->SetStringField(TEXT("objectName"), Target->GetName());
		Result->SetStringField(
			TEXT("ownerClass"),
			Target->GetClass() != nullptr ? Target->GetClass()->GetPathName() : FString());
		if (bComponentTarget)
		{
			Result->SetStringField(TEXT("componentOwnerPath"), Actor->GetPathName());
		}
		Result->SetStringField(TEXT("localRoleName"), NetRoleShortName(Actor->GetLocalRole()));
		Result->SetBoolField(TEXT("hasAuthority"), Actor->HasAuthority());
		Result->SetNumberField(TEXT("maxStringChars"), MaxChars);
		Result->SetNumberField(TEXT("requestedCount"), Request.PropertyNames.Num());

		TArray<TSharedPtr<FJsonValue>> Items;
		for (const FString& Name : Request.PropertyNames)
		{
			const FProperty* Property = Target->GetClass() != nullptr
				? Target->GetClass()->FindPropertyByName(FName(*Name))
				: nullptr;
			if (Property == nullptr)
			{
				Items.Add(MakeShared<FJsonValueObject>(
					MakeScalarFailure(Name, TEXT("unknown"), TEXT("property-not-found"), FString())));
				continue;
			}
			if (!IsExposedScalarProperty(Property))
			{
				Items.Add(MakeShared<FJsonValueObject>(
					MakeScalarFailure(
						Name,
						TEXT("not-exposed"),
						TEXT("not-edit-or-blueprint-visible"),
						Property->GetCPPType())));
				continue;
			}
			Items.Add(MakeShared<FJsonValueObject>(ReadScalarProperty(Property, Target, MaxChars)));
		}
		Result->SetNumberField(TEXT("returnedCount"), Items.Num());
		Result->SetArrayField(TEXT("properties"), Items);
		OutResult = Result;
		return true;
	}
}

TSharedRef<FJsonObject> FUEAgentKitEditorBridge::BuildPieWorldContextsResult(
	const TSharedPtr<FJsonObject>& Params) const
{
	int32 MaxContexts = UEAgentKitPieRuntime::DefaultMaxContexts;
	double RequestedMaxContexts = 0.0;
	if (Params.IsValid() && Params->TryGetNumberField(TEXT("maxContexts"), RequestedMaxContexts))
	{
		MaxContexts = static_cast<int32>(RequestedMaxContexts);
	}
	return UEAgentKitPieRuntime::BuildWorldContextsJson(MaxContexts);
}

bool FUEAgentKitEditorBridge::TryInspectPieActorResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	using namespace UEAgentKitPieRuntime;

	OutResult.Reset();
	OutErrorCode.Reset();
	OutErrorMessage.Reset();
	if (!Params.IsValid())
	{
		OutErrorCode = FailureCode(EFailure::InvalidRequest);
		OutErrorMessage = TEXT("editor.inspectPieActor requires an exact pieInstanceId and one Actor identity.");
		return false;
	}

	FActorInspectionRequest Request;
	double PieInstanceId = 0.0;
	if (!Params->TryGetNumberField(TEXT("pieInstanceId"), PieInstanceId))
	{
		OutErrorCode = FailureCode(EFailure::InvalidRequest);
		OutErrorMessage = TEXT("pieInstanceId must be the exact PIE instance id reported by editor.getPieWorldContexts (0 is the primary instance).");
		return false;
	}
	Request.PieInstanceId = static_cast<int32>(PieInstanceId);
	Params->TryGetStringField(TEXT("worldPath"), Request.WorldPath);
	Params->TryGetStringField(TEXT("actorPath"), Request.ActorPath);
	Params->TryGetStringField(TEXT("actorGuid"), Request.ActorGuid);
	Params->TryGetStringField(TEXT("actorName"), Request.ActorName);
	Params->TryGetStringField(TEXT("actorLabel"), Request.ActorLabel);
	Params->TryGetStringField(TEXT("actorClassPath"), Request.ActorClassPath);
	double NumberValue = 0.0;
	if (Params->TryGetNumberField(TEXT("maxComponents"), NumberValue))
	{
		Request.MaxComponents = static_cast<int32>(NumberValue);
	}
	if (Params->TryGetNumberField(TEXT("maxAssetsPerComponent"), NumberValue))
	{
		Request.MaxComponentAssets = static_cast<int32>(NumberValue);
	}
	return TryDescribePieActor(Request, OutResult, OutErrorCode, OutErrorMessage);
}

bool FUEAgentKitEditorBridge::TryReadPieScalarsResult(
	const TSharedPtr<FJsonObject>& Params,
	TSharedPtr<FJsonObject>& OutResult,
	FString& OutErrorCode,
	FString& OutErrorMessage) const
{
	using namespace UEAgentKitPieRuntime;

	OutResult.Reset();
	OutErrorCode.Reset();
	OutErrorMessage.Reset();
	if (!Params.IsValid())
	{
		OutErrorCode = FailureCode(EFailure::InvalidRequest);
		OutErrorMessage = TEXT("editor.getPieScalars requires an exact pieInstanceId, one Actor identity and propertyNames.");
		return false;
	}

	FScalarInspectionRequest Request;
	double PieInstanceId = 0.0;
	if (!Params->TryGetNumberField(TEXT("pieInstanceId"), PieInstanceId))
	{
		OutErrorCode = FailureCode(EFailure::InvalidRequest);
		OutErrorMessage = TEXT("pieInstanceId must be the exact PIE instance id reported by editor.getPieWorldContexts (0 is the primary instance).");
		return false;
	}
	Request.Actor.PieInstanceId = static_cast<int32>(PieInstanceId);
	Params->TryGetStringField(TEXT("worldPath"), Request.Actor.WorldPath);
	Params->TryGetStringField(TEXT("actorPath"), Request.Actor.ActorPath);
	Params->TryGetStringField(TEXT("actorGuid"), Request.Actor.ActorGuid);
	Params->TryGetStringField(TEXT("actorName"), Request.Actor.ActorName);
	Params->TryGetStringField(TEXT("actorLabel"), Request.Actor.ActorLabel);
	Params->TryGetStringField(TEXT("componentPath"), Request.ComponentPath);
	Params->TryGetStringField(TEXT("componentName"), Request.ComponentName);
	Params->TryGetStringField(TEXT("componentClassPath"), Request.ComponentClassPath);

	const TArray<TSharedPtr<FJsonValue>>* NameValues = nullptr;
	if (!Params->TryGetArrayField(TEXT("propertyNames"), NameValues) || NameValues == nullptr)
	{
		OutErrorCode = FailureCode(EFailure::InvalidRequest);
		OutErrorMessage = TEXT("propertyNames must be an explicit array of 1 to 8 plain property identifiers.");
		return false;
	}
	for (const TSharedPtr<FJsonValue>& NameValue : *NameValues)
	{
		FString Name;
		if (!NameValue.IsValid() || !NameValue->TryGetString(Name))
		{
			OutErrorCode = FailureCode(EFailure::InvalidRequest);
			OutErrorMessage = TEXT("Every propertyNames entry must be a string.");
			return false;
		}
		Request.PropertyNames.Add(Name);
	}

	double NumberValue = 0.0;
	if (Params->TryGetNumberField(TEXT("maxStringChars"), NumberValue))
	{
		Request.MaxStringChars = static_cast<int32>(NumberValue);
	}
	return TryReadPieScalars(Request, OutResult, OutErrorCode, OutErrorMessage);
}

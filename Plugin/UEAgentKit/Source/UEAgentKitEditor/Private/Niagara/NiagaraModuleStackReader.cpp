#include "Niagara/NiagaraModuleStackReader.h"

#include "NiagaraCommon.h"
#include "NiagaraScript.h"
#include "NiagaraSystem.h"
#include "NiagaraTypes.h"

#include "NiagaraNodeFunctionCall.h"
#include "NiagaraNodeOutput.h"

#include "ViewModels/NiagaraEmitterHandleViewModel.h"
#include "ViewModels/NiagaraSystemViewModel.h"
#include "ViewModels/Stack/NiagaraParameterHandle.h"
#include "ViewModels/Stack/NiagaraStackEntry.h"
#include "ViewModels/Stack/NiagaraStackFunctionInput.h"
#include "ViewModels/Stack/NiagaraStackModuleItem.h"
#include "ViewModels/Stack/NiagaraStackScriptItemGroup.h"
#include "ViewModels/Stack/NiagaraStackViewModel.h"

#include "Misc/PackageName.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

namespace UEAgentKitNiagaraStack
{
	namespace
	{
		const TCHAR* const StageOtherName = TEXT("Other");
		const TCHAR* const EmptyString = TEXT("");

		/** Spec stage bucket for one script usage. */
		const TCHAR* StageNameForUsage(const ENiagaraScriptUsage Usage)
		{
			switch (Usage)
			{
			case ENiagaraScriptUsage::SystemSpawnScript: return TEXT("SystemSpawn");
			case ENiagaraScriptUsage::SystemUpdateScript: return TEXT("SystemUpdate");
			case ENiagaraScriptUsage::EmitterSpawnScript: return TEXT("EmitterSpawn");
			case ENiagaraScriptUsage::EmitterUpdateScript: return TEXT("EmitterUpdate");
			// The interpolated spawn script is the same "Particle Spawn" row in the stack UI.
			case ENiagaraScriptUsage::ParticleSpawnScript:
			case ENiagaraScriptUsage::ParticleSpawnScriptInterpolated: return TEXT("ParticleSpawn");
			case ENiagaraScriptUsage::ParticleUpdateScript: return TEXT("ParticleUpdate");
			default: return StageOtherName;
			}
		}

		FString UsageName(const ENiagaraScriptUsage Usage)
		{
			if (const UEnum* Enum = StaticEnum<ENiagaraScriptUsage>())
			{
				return Enum->GetNameStringByValue(static_cast<int64>(Usage));
			}
			return FString();
		}

		const TCHAR* ValueModeName(const UNiagaraStackFunctionInput::EValueMode Mode)
		{
			switch (Mode)
			{
			case UNiagaraStackFunctionInput::EValueMode::Local: return TEXT("Local");
			case UNiagaraStackFunctionInput::EValueMode::Linked: return TEXT("Linked");
			case UNiagaraStackFunctionInput::EValueMode::Dynamic: return TEXT("Dynamic");
			case UNiagaraStackFunctionInput::EValueMode::Data: return TEXT("Data");
			case UNiagaraStackFunctionInput::EValueMode::ObjectAsset: return TEXT("ObjectAsset");
			case UNiagaraStackFunctionInput::EValueMode::Expression: return TEXT("Expression");
			case UNiagaraStackFunctionInput::EValueMode::DefaultFunction: return TEXT("DefaultFunction");
			case UNiagaraStackFunctionInput::EValueMode::InvalidOverride: return TEXT("InvalidOverride");
			case UNiagaraStackFunctionInput::EValueMode::UnsupportedDefault: return TEXT("UnsupportedDefault");
			case UNiagaraStackFunctionInput::EValueMode::None: break;
			}
			return TEXT("None");
		}

		/**
		 * Read `Count` numeric components out of a Niagara struct payload.
		 * Niagara uses float and double (LWC) variants of the same logical type, so
		 * the component width is inferred from the payload size rather than assumed.
		 */
		bool ReadComponents(const uint8* Data, const int32 Size, const int32 Count, TArray<double>& Out)
		{
			Out.Reset();
			if (Data == nullptr || Count <= 0)
			{
				return false;
			}

			if (Size == Count * static_cast<int32>(sizeof(double)))
			{
				Out.Reserve(Count);
				for (int32 Index = 0; Index < Count; ++Index)
				{
					double Value = 0.0;
					FMemory::Memcpy(&Value, Data + Index * sizeof(double), sizeof(double));
					Out.Add(Value);
				}
				return true;
			}

			if (Size == Count * static_cast<int32>(sizeof(float)))
			{
				Out.Reserve(Count);
				for (int32 Index = 0; Index < Count; ++Index)
				{
					float Value = 0.0f;
					FMemory::Memcpy(&Value, Data + Index * sizeof(float), sizeof(float));
					Out.Add(static_cast<double>(Value));
				}
				return true;
			}

			return false;
		}

		TSharedPtr<FJsonValue> MakeComponentObject(
			const uint8* Data,
			const int32 Size,
			const TCHAR* const* Names,
			const int32 Count)
		{
			TArray<double> Components;
			if (!ReadComponents(Data, Size, Count, Components))
			{
				return nullptr;
			}

			const TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
			for (int32 Index = 0; Index < Count; ++Index)
			{
				Object->SetNumberField(Names[Index], Components[Index]);
			}
			return MakeShared<FJsonValueObject>(Object);
		}

		/**
		 * Convert a Local input payload into a JSON value when the shape is one the
		 * Agent can reason about.  Anything else is only reported as valueText so we
		 * never invent a number for exotic struct payloads.
		 */
		TSharedPtr<FJsonValue> MakeTypedLocalValue(
			const FNiagaraTypeDefinition& Type,
			const uint8* Data,
			const int32 Size,
			bool& bOutRepresentable)
		{
			bOutRepresentable = false;
			if (Data == nullptr || Size <= 0)
			{
				return nullptr;
			}

			// Compare by the concrete UScriptStruct rather than by display name:
			// FNiagaraTypeDefinition::GetName() returns the struct short name
			// ("NiagaraFloat", "Vector", "LinearColor", ...), not a friendly type name.
			const UStruct* Struct = Type.GetStruct();
			auto Matches = [Struct](const UScriptStruct* Known) { return Struct != nullptr && Struct == Known; };

			if (Matches(FNiagaraTypeDefinition::GetFloatStruct()) && Size == static_cast<int32>(sizeof(float)))
			{
				float Value = 0.0f;
				FMemory::Memcpy(&Value, Data, sizeof(float));
				bOutRepresentable = true;
				return MakeShared<FJsonValueNumber>(static_cast<double>(Value));
			}

			// TypeDefinition::GetStruct() maps enums onto the int struct, so enum
			// inputs are reported as their numeric value rather than dropped.
			if (Matches(FNiagaraTypeDefinition::GetIntStruct()) && Size == static_cast<int32>(sizeof(int32)))
			{
				int32 Value = 0;
				FMemory::Memcpy(&Value, Data, sizeof(int32));
				bOutRepresentable = true;
				return MakeShared<FJsonValueNumber>(static_cast<double>(Value));
			}

			// FNiagaraBool stores 0xFFFFFFFF for true and 0 for false.
			if (Matches(FNiagaraTypeDefinition::GetBoolStruct()) && Size == static_cast<int32>(sizeof(int32)))
			{
				int32 Value = 0;
				FMemory::Memcpy(&Value, Data, sizeof(int32));
				bOutRepresentable = true;
				return MakeShared<FJsonValueBoolean>(Value != 0);
			}

			static const TCHAR* const Vec2Names[] = { TEXT("x"), TEXT("y") };
			static const TCHAR* const Vec3Names[] = { TEXT("x"), TEXT("y"), TEXT("z") };
			static const TCHAR* const Vec4Names[] = { TEXT("x"), TEXT("y"), TEXT("z"), TEXT("w") };
			static const TCHAR* const ColorNames[] = { TEXT("r"), TEXT("g"), TEXT("b"), TEXT("a") };

			const bool bIsVec2 = Matches(FNiagaraTypeDefinition::GetVec2Struct());
			const bool bIsVec3 = Matches(FNiagaraTypeDefinition::GetVec3Struct())
				|| Matches(FNiagaraTypeDefinition::GetPositionStruct());
			const bool bIsVec4 = Matches(FNiagaraTypeDefinition::GetVec4Struct())
				|| Matches(FNiagaraTypeDefinition::GetQuatStruct());
			const bool bIsColor = Matches(FNiagaraTypeDefinition::GetColorStruct());

			if (bIsVec2)
			{
				TSharedPtr<FJsonValue> Value = MakeComponentObject(Data, Size, Vec2Names, 2);
				bOutRepresentable = Value.IsValid();
				return Value;
			}

			if (bIsVec3)
			{
				TSharedPtr<FJsonValue> Value = MakeComponentObject(Data, Size, Vec3Names, 3);
				bOutRepresentable = Value.IsValid();
				return Value;
			}

			if (bIsVec4)
			{
				TSharedPtr<FJsonValue> Value = MakeComponentObject(Data, Size, Vec4Names, 4);
				bOutRepresentable = Value.IsValid();
				return Value;
			}

			if (bIsColor)
			{
				TSharedPtr<FJsonValue> Value = MakeComponentObject(Data, Size, ColorNames, 4);
				bOutRepresentable = Value.IsValid();
				return Value;
			}

			return nullptr;
		}

		FString SafeObjectPath(const UObject* Object)
		{
			return Object != nullptr ? Object->GetPathName() : FString(EmptyString);
		}

		/** One execution stage bucket, in first-seen stack order. */
		struct FStageBucket
		{
			FString StageName;
			FString UsageName;
			TArray<TSharedPtr<FJsonValue>> Modules;
		};

		/**
		 * Walks one stack root and groups module items by their enclosing script
		 * item group. Order of stages and modules is the stack order exactly as the
		 * engine built it; nothing is sorted or de-duplicated here.
		 */
		class FStackWalker
		{
		public:
			explicit FStackWalker(const FReadRequest& InRequest)
				: Request(InRequest)
			{
			}

			void Walk(UNiagaraStackEntry& Entry, const FString& CurrentStage, const int32 Depth)
			{
				if (bLimitReached || Depth > 12)
				{
					return;
				}

				FString Stage = CurrentStage;

				if (const UNiagaraStackScriptItemGroup* ScriptGroup = Cast<UNiagaraStackScriptItemGroup>(&Entry))
				{
					const UNiagaraNodeOutput* OutputNode = ScriptGroup->GetScriptOutputNode();
					if (OutputNode != nullptr)
					{
						const ENiagaraScriptUsage Usage = OutputNode->GetUsage();
						Stage = StageNameForUsage(Usage);
						FindOrAddStage(Stage, UsageName(Usage));
					}
				}

				if (const UNiagaraStackModuleItem* ModuleItem = Cast<UNiagaraStackModuleItem>(&Entry))
				{
					AppendModule(*ModuleItem, Stage);
					return;
				}

				TArray<UNiagaraStackEntry*> Children;
				Entry.GetFilteredChildren(Children);
				for (UNiagaraStackEntry* Child : Children)
				{
					if (Child != nullptr)
					{
						Walk(*Child, Stage, Depth + 1);
						if (bLimitReached)
						{
							return;
						}
					}
				}
			}

			TArray<TSharedPtr<FJsonValue>> BuildStageArray() const
			{
				TArray<TSharedPtr<FJsonValue>> Result;
				for (const FStageBucket& Bucket : Stages)
				{
					const TSharedRef<FJsonObject> StageObject = MakeShared<FJsonObject>();
					StageObject->SetStringField(TEXT("stage"), Bucket.StageName);
					StageObject->SetStringField(TEXT("usage"), Bucket.UsageName);
					StageObject->SetNumberField(TEXT("moduleCount"), Bucket.Modules.Num());
					StageObject->SetArrayField(TEXT("modules"), Bucket.Modules);
					Result.Add(MakeShared<FJsonValueObject>(StageObject));
				}
				return Result;
			}

			int32 GetModuleCount() const { return TotalModules; }
			bool IsTruncated() const { return bLimitReached; }

		private:
			FStageBucket& FindOrAddStage(const FString& StageName, const FString& Usage)
			{
				if (const int32* Found = StageIndices.Find(StageName))
				{
					return Stages[*Found];
				}

				FStageBucket Bucket;
				Bucket.StageName = StageName;
				Bucket.UsageName = Usage;
				const int32 Index = Stages.Add(MoveTemp(Bucket));
				StageIndices.Add(StageName, Index);
				return Stages[Index];
			}

			void AppendModule(const UNiagaraStackModuleItem& ModuleItem, const FString& StageName)
			{
				if (TotalModules >= Request.MaxModules)
				{
					bLimitReached = true;
					return;
				}

				FStageBucket& Bucket = FindOrAddStage(StageName, FString());
				const int32 OrderInStage = Bucket.Modules.Num();

				UNiagaraNodeFunctionCall& ModuleNode = ModuleItem.GetModuleNode();
				UObject* ExternalAsset = ModuleItem.GetExternalAsset();
				UObject* ScriptObject = ExternalAsset != nullptr
					? ExternalAsset
					: static_cast<UObject*>(ModuleNode.FunctionScript.Get());

				const TSharedRef<FJsonObject> ModuleObject = MakeShared<FJsonObject>();
				ModuleObject->SetNumberField(TEXT("order"), OrderInStage);
				ModuleObject->SetNumberField(TEXT("engineModuleIndex"), ModuleItem.GetModuleIndex());
				ModuleObject->SetStringField(TEXT("name"), ModuleItem.GetDisplayName().ToString());
				ModuleObject->SetStringField(TEXT("scriptPath"), SafeObjectPath(ScriptObject));
				ModuleObject->SetBoolField(TEXT("enabled"), ModuleItem.GetIsEnabled());
				ModuleObject->SetStringField(
					TEXT("instanceIdentity"),
					ModuleNode.NodeGuid.ToString(EGuidFormats::DigitsWithHyphensLower));

				if (Request.bIncludeInputs)
				{
					TArray<UNiagaraStackFunctionInput*> Inputs;
					ModuleItem.GetParameterInputs(Inputs);

					TArray<TSharedPtr<FJsonValue>> InputValues;
					bool bInputsTruncated = false;
					for (UNiagaraStackFunctionInput* Input : Inputs)
					{
						if (Input == nullptr)
						{
							continue;
						}
						if (InputValues.Num() >= Request.MaxInputsPerModule)
						{
							bInputsTruncated = true;
							break;
						}
						InputValues.Add(MakeShared<FJsonValueObject>(DescribeInput(*Input)));
					}

					ModuleObject->SetNumberField(TEXT("inputCount"), Inputs.Num());
					ModuleObject->SetNumberField(TEXT("reportedInputCount"), InputValues.Num());
					ModuleObject->SetBoolField(TEXT("inputsTruncated"), bInputsTruncated);
					ModuleObject->SetArrayField(TEXT("inputs"), InputValues);
				}

				Bucket.Modules.Add(MakeShared<FJsonValueObject>(ModuleObject));
				++TotalModules;
			}

			static TSharedRef<FJsonObject> DescribeInput(UNiagaraStackFunctionInput& Input)
			{
				const UNiagaraStackFunctionInput::EValueMode Mode = Input.GetValueMode();
				const FNiagaraTypeDefinition& InputType = Input.GetInputType();

				const TSharedRef<FJsonObject> InputObject = MakeShared<FJsonObject>();
				InputObject->SetStringField(TEXT("name"), Input.GetDisplayName().ToString());
				InputObject->SetStringField(
					TEXT("handle"),
					Input.GetInputParameterHandle().GetParameterHandleString().ToString());
				InputObject->SetStringField(TEXT("type"), InputType.GetName());
				InputObject->SetStringField(TEXT("source"), ValueModeName(Mode));
				InputObject->SetBoolField(TEXT("enabled"), Input.GetIsEnabled());

				switch (Mode)
				{
				case UNiagaraStackFunctionInput::EValueMode::Local:
				{
					// GetLocalValueStruct() is non-const in 5.6.1 even though it only reads.
					const TSharedPtr<const FStructOnScope> LocalValue = Input.GetLocalValueStruct();
					if (LocalValue.IsValid() && LocalValue->GetStructMemory() != nullptr)
					{
						const uint8* Data = LocalValue->GetStructMemory();
						const int32 Size = InputType.GetSize();

						bool bRepresentable = false;
						const TSharedPtr<FJsonValue> TypedValue = MakeTypedLocalValue(InputType, Data, Size, bRepresentable);
						if (bRepresentable && TypedValue.IsValid())
						{
							InputObject->SetField(TEXT("value"), TypedValue);
						}
						InputObject->SetBoolField(TEXT("valueRepresentable"), bRepresentable);

						// FNiagaraTypeHelper::ToString handles float/int/bool directly
						// and falls back to property iteration for structs, which keeps
						// the text in the same shape the Niagara editor shows.
						if (const UStruct* Struct = InputType.GetStruct())
						{
							InputObject->SetStringField(TEXT("valueText"), FNiagaraTypeHelper::ToString(Data, Struct));
						}
					}
					else
					{
						InputObject->SetBoolField(TEXT("valueRepresentable"), false);
					}
					break;
				}
				case UNiagaraStackFunctionInput::EValueMode::Linked:
				{
					const FNiagaraVariableBase& LinkedParameter = Input.GetLinkedParameterValue();
					InputObject->SetStringField(TEXT("linkedTarget"), LinkedParameter.GetName().ToString());
					InputObject->SetStringField(TEXT("linkedTargetType"), LinkedParameter.GetType().GetName());
					break;
				}
				case UNiagaraStackFunctionInput::EValueMode::Dynamic:
				{
					const UNiagaraNodeFunctionCall* DynamicNode = Input.GetDynamicInputNode();
					InputObject->SetStringField(
						TEXT("dynamicInput"),
						DynamicNode != nullptr ? DynamicNode->GetFunctionName() : FString(EmptyString));
					break;
				}
				case UNiagaraStackFunctionInput::EValueMode::Data:
				{
					const UNiagaraDataInterface* DataInterface = Input.GetDataValueObject();
					InputObject->SetStringField(TEXT("dataInterfaceClass"), DataInterface != nullptr ? DataInterface->GetClass()->GetPathName() : FString(EmptyString));
					break;
				}
				case UNiagaraStackFunctionInput::EValueMode::ObjectAsset:
				{
					InputObject->SetStringField(TEXT("objectAssetPath"), SafeObjectPath(Input.GetObjectAssetValue()));
					break;
				}
				case UNiagaraStackFunctionInput::EValueMode::Expression:
				{
					InputObject->SetStringField(TEXT("expression"), Input.GetCustomExpressionText().ToString());
					break;
				}
				default:
					break;
				}

				return InputObject;
			}

			const FReadRequest& Request;
			TArray<FStageBucket> Stages;
			TMap<FString, int32> StageIndices;
			int32 TotalModules = 0;
			bool bLimitReached = false;
		};

		TSharedRef<FJsonObject> BuildEmitterObject(
			const FReadRequest& Request,
			FNiagaraEmitterHandleViewModel& EmitterHandle,
			const int32 EmitterIndex)
		{
			const TSharedRef<FJsonObject> EmitterObject = MakeShared<FJsonObject>();
			EmitterObject->SetNumberField(TEXT("index"), EmitterIndex);
			EmitterObject->SetStringField(TEXT("name"), EmitterHandle.GetName().ToString());
			EmitterObject->SetStringField(
				TEXT("handleId"),
				EmitterHandle.GetId().ToString(EGuidFormats::DigitsWithHyphensLower));
			EmitterObject->SetBoolField(TEXT("enabled"), EmitterHandle.GetIsEnabled());

			UNiagaraStackViewModel* StackViewModel = EmitterHandle.GetEmitterStackViewModel();
			if (StackViewModel == nullptr)
			{
				EmitterObject->SetBoolField(TEXT("stackAvailable"), false);
				EmitterObject->SetNumberField(TEXT("moduleCount"), 0);
				EmitterObject->SetBoolField(TEXT("truncated"), false);
				EmitterObject->SetArrayField(TEXT("stages"), TArray<TSharedPtr<FJsonValue>>());
				return EmitterObject;
			}

			UNiagaraStackEntry* RootEntry = StackViewModel->GetRootEntry();
			if (RootEntry == nullptr)
			{
				EmitterObject->SetBoolField(TEXT("stackAvailable"), false);
				EmitterObject->SetNumberField(TEXT("moduleCount"), 0);
				EmitterObject->SetBoolField(TEXT("truncated"), false);
				EmitterObject->SetArrayField(TEXT("stages"), TArray<TSharedPtr<FJsonValue>>());
				return EmitterObject;
			}

			// Rebuild the children tree synchronously: the stack normally refreshes
			// from Tick(), and GetFilteredChildren() only filters an existing tree.
			RootEntry->RefreshChildren();

			FStackWalker Walker(Request);
			Walker.Walk(*RootEntry, FString(StageOtherName), 0);

			EmitterObject->SetBoolField(TEXT("stackAvailable"), true);
			EmitterObject->SetNumberField(TEXT("moduleCount"), Walker.GetModuleCount());
			EmitterObject->SetBoolField(TEXT("truncated"), Walker.IsTruncated());
			EmitterObject->SetArrayField(TEXT("stages"), Walker.BuildStageArray());
			return EmitterObject;
		}
	}

	const TCHAR* FailureCode(const EReadFailure Failure)
	{
		switch (Failure)
		{
		case EReadFailure::InvalidRequest: return TEXT("invalid-request");
		case EReadFailure::SystemNotFound: return TEXT("niagara-system-not-found");
		case EReadFailure::SystemCompiling: return TEXT("niagara-system-compiling");
		case EReadFailure::StackUnavailable: return TEXT("niagara-stack-unavailable");
		case EReadFailure::None: break;
		}
		return TEXT("live-editor-action-failed");
	}

	bool ReadModuleStack(
		const FReadRequest& Request,
		TSharedPtr<FJsonObject>& OutResult,
		EReadFailure& OutFailure,
		FString& OutMessage)
	{
		OutResult.Reset();
		OutFailure = EReadFailure::None;
		OutMessage.Reset();

		if (Request.SystemPath.IsEmpty() || !Request.SystemPath.StartsWith(TEXT("/")))
		{
			OutFailure = EReadFailure::InvalidRequest;
			OutMessage = TEXT("systemPath must be an exact /Game package path of a Niagara System.");
			return false;
		}
		if (Request.MaxModules <= 0)
		{
			OutFailure = EReadFailure::InvalidRequest;
			OutMessage = TEXT("maxModules must be greater than zero.");
			return false;
		}
		if (Request.MaxInputsPerModule < 0)
		{
			OutFailure = EReadFailure::InvalidRequest;
			OutMessage = TEXT("maxInputsPerModule must not be negative.");
			return false;
		}

		const FSoftObjectPath SoftSystemPath(Request.SystemPath);
		UNiagaraSystem* System = Cast<UNiagaraSystem>(SoftSystemPath.TryLoad());
		if (System == nullptr)
		{
			// Accept a bare package path (/Game/Dev/NS_DevStack) as well as an exact
			// object path (/Game/Dev/NS_DevStack.NS_DevStack).
			if (!Request.SystemPath.Contains(TEXT(".")))
			{
				const FString ObjectPath = Request.SystemPath + TEXT(".") + FPackageName::GetShortName(Request.SystemPath);
				System = Cast<UNiagaraSystem>(FSoftObjectPath(ObjectPath).TryLoad());
			}
		}
		if (System == nullptr)
		{
			OutFailure = EReadFailure::SystemNotFound;
			OutMessage = FString::Printf(
				TEXT("No Niagara System asset was found at '%s'."), *Request.SystemPath);
			return false;
		}

		// Loading a Niagara System queues the engine's own post-load compile, so the
		// first read almost always starts with a pending request.  Completing it is a
		// readiness wait (UNiagaraSystem::WaitForCompilationComplete), not a new edit:
		// it never dirties the package and never saves.  The view model build below
		// waits for the same compilation anyway.
		if (System->HasOutstandingCompilationRequests())
		{
			System->WaitForCompilationComplete(/*bIncludingGPUShaders*/ false, /*bShowProgress*/ false);
		}
		if (System->HasOutstandingCompilationRequests())
		{
			OutFailure = EReadFailure::SystemCompiling;
			OutMessage = FString::Printf(
				TEXT("Niagara System '%s' still has outstanding compilation requests after waiting."), *Request.SystemPath);
			return false;
		}

		// Always build a data-processing only view model (no preview scene, no
		// sequencer, no undo registration), mirroring UNiagaraSystemAuditCommandlet.
		//
		// FNiagaraSystemViewModel::GetExistingViewModelForObject() cannot be used
		// here: its backing static TNiagaraViewModelManager<...>::ObjectsToViewModels
		// is explicitly instantiated inside the NiagaraEditor module without an
		// export macro, so calling it from another module is a link error.
		TSharedPtr<FNiagaraSystemViewModel> SystemViewModel = MakeShared<FNiagaraSystemViewModel>();

		FNiagaraSystemViewModelOptions Options;
		Options.EditMode = ENiagaraSystemViewModelEditMode::SystemAsset;
		Options.bIsForDataProcessingOnly = true;
		Options.bCanAutoCompile = false;
		Options.bCanSimulate = false;
		Options.bCanModifyEmittersFromTimeline = false;
		Options.MessageLogGuid = System->GetAssetGuid();

		// Initialize() ends with RefreshAll(), which creates the emitter handle view
		// models and their per-emitter stack view models.
		SystemViewModel->Initialize(*System, Options);

		const TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
		Result->SetStringField(TEXT("systemPath"), Request.SystemPath);
		Result->SetBoolField(TEXT("loaded"), true);
		Result->SetBoolField(TEXT("compiled"), !System->HasOutstandingCompilationRequests());
		Result->SetStringField(
			TEXT("systemAssetPath"),
			SafeObjectPath(System));

		// System level stages (System Spawn / System Update) live on the system stack.
		TArray<TSharedPtr<FJsonValue>> SystemStages;
		if (UNiagaraStackViewModel* SystemStackViewModel = SystemViewModel->GetSystemStackViewModel())
		{
			if (UNiagaraStackEntry* SystemRootEntry = SystemStackViewModel->GetRootEntry())
			{
				SystemRootEntry->RefreshChildren();
				FStackWalker SystemWalker(Request);
				SystemWalker.Walk(*SystemRootEntry, FString(StageOtherName), 0);
				SystemStages = SystemWalker.BuildStageArray();
			}
		}
		Result->SetArrayField(TEXT("systemStages"), SystemStages);

		TArray<TSharedPtr<FJsonValue>> Emitters;
		bool bAnyEmitterTruncated = false;
		const TArray<TSharedRef<FNiagaraEmitterHandleViewModel>>& EmitterHandles =
			SystemViewModel->GetEmitterHandleViewModels();
		for (int32 EmitterIndex = 0; EmitterIndex < EmitterHandles.Num(); ++EmitterIndex)
		{
			const TSharedRef<FJsonObject> EmitterObject =
				BuildEmitterObject(Request, EmitterHandles[EmitterIndex].Get(), EmitterIndex);

			bool bEmitterTruncated = false;
			EmitterObject->TryGetBoolField(TEXT("truncated"), bEmitterTruncated);
			bAnyEmitterTruncated |= bEmitterTruncated;

			Emitters.Add(MakeShared<FJsonValueObject>(EmitterObject));
		}

		Result->SetNumberField(TEXT("emitterCount"), Emitters.Num());
		Result->SetArrayField(TEXT("emitters"), Emitters);
		Result->SetBoolField(TEXT("truncated"), bAnyEmitterTruncated);
		Result->SetNumberField(TEXT("maxModulesPerEmitter"), Request.MaxModules);
		Result->SetNumberField(TEXT("maxInputsPerModule"), Request.MaxInputsPerModule);
		Result->SetBoolField(TEXT("includeInputs"), Request.bIncludeInputs);

		// The destructor unregisters the view model and runs Cleanup(), which also
		// keeps TNiagaraViewModelManager::CleanAll() happy at module shutdown.
		SystemViewModel.Reset();

		OutResult = Result;
		return true;
	}
}
